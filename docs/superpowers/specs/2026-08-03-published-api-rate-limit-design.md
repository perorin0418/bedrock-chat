# 公開API APIキー単位レートリミット 設計書

## 背景・目的

既存のユーザー毎コストレートリミット([2026-08-03-rate-limit-design.md](./2026-08-03-rate-limit-design.md))は、公開bot API(`published_api.py` / `sqs_consumer.py`)を対象外としていた。理由は、公開APIの呼び出し元を「どのユーザーの利用か」に紐づける仕組みが存在せず、bot単位(`PUBLISHED_API#{bot_id}`という擬似ユーザー)でしか区別できなかったため。

今回、以下を実現する。

1. 公開APIのAPIキーを作成する際、そのキーを作成したユーザーとの紐づけを必須にする(紐づけなしでのキー作成は不可)。
2. そのAPIキー経由の利用コストを、紐づけられたユーザーの既存レートリミット台帳(直近5時間$10 / 直近7日間$336)にそのまま合算する。
3. 結果として、あるユーザーが自分の発行した公開APIキーを使いすぎた場合、そのユーザー自身の通常チャット(WebSocket / REST)もブロックされるようになる。

## 前提・既存実装の確認結果

- APIキーはAPI Gateway上にのみ存在し(`create_api_key`)、アプリ側DBには一切の付随情報を持たない。`ApiKeyInput`は`description`のみ。
- 公開APIは2種類のLambdaで構成される: 同期処理を行う`apiHandler`(FastAPI, `routes/published_api.py`)と非同期処理を行う`sqsConsumeHandler`(`sqs_consumer.py`)。`POST /conversation`はSQSにメッセージを積むだけで即座に200を返し、実際の`chat()`呼び出し・コスト確定・`record_usage()`は`sqs_consumer.py`側で行われる。
- 公開APIの`current_user`は常に`User.from_published_api_id(bot_id)`で作られる擬似ユーザー(`PUBLISHED_API#{bot_id}`)であり、呼び出し元のAPIキーを区別しない。
- バックエンドはLambda Web Adapter(`aws-lambda-adapter`)経由でuvicornに転送されている。Adapterのソース(`src/lib.rs`)を確認した結果、Lambdaに渡されたイベントの`request_context`をJSON化して`x-amzn-request-context`ヘッダーに必ず付与することを確認した(要出典: `aws/aws-lambda-web-adapter` リポジトリの`src/lib.rs`)。API Gateway REST APIのプロキシ統合(`LambdaRestApi`, `apiKeyRequired: true`)の場合、この`request_context.identity`には`api_key_id`(JSON上は`apiKeyId`)が含まれる(`aws/aws-lambda-rust-runtime`の`lambda-events/src/event/apigw/mod.rs`で確認)。よって、アプリ側で`x-amzn-request-context`ヘッダーをパースすれば、リクエストに使われたAPIキーIDを追加のAWS API呼び出しなしで取得できる。
- 既存のDynamoDBテーブル群(`conversationTable`, `botTable`, `usageLedgerTable`)は、CDKの`Database`コンストラクトで作成され`tableAccessRole`(STS AssumeRoleセッションポリシー方式)に読み書き権限を付与、テーブル名は`bedrock-chat-stack.ts`でCfnOutput(`exportName`付き)としてexportし、独立デプロイの`ApiPublishmentStack`(`cdk/bin/api-publish.ts`)側で`Fn.importValue`によりimportする、という確立されたパターンがある。新規テーブル・既存のレートリミットSSMパラメータも同じパターンで配線する。
- `usageLedgerTable`と2つのレートリミットSSM パラメータは、現状`ApiPublishmentStack`側には配線されていない(前回設計時に将来課題として意図的に見送られていた)。今回、これらを`ApiPublishmentStack`側にも配線する。

## アーキテクチャ

### 1. APIキー ⇔ ユーザー紐づけ: 新規DynamoDBテーブル `ApiKeyOwnerTable`

- `cdk/lib/constructs/database.ts`に追加。PK: `ApiKeyId`(string)。属性: `UserId`(string)、`CreateTime`(number)。TTLなし(キー削除時に明示的に行削除)。
- `BillingMode.PAY_PER_REQUEST`、`tableAccessRole.grantReadWriteData(apiKeyOwnerTable)`。
- 既存パターンに倣い、`bedrock-chat-stack.ts`で`ApiKeyOwnerTableName`としてexport(`exportName`付き)。

### 2. APIキー作成時の紐づけ強制

- 新規`backend/app/repositories/api_key_owner.py`:
  - `bind_api_key_owner(api_key_id: str, user_id: str) -> None`: put_item。ベストエフォートにしない(紐づけ失敗を握りつぶすと「強制」の意味がなくなるため)。
  - `find_api_key_owner(api_key_id: str) -> str | None`: get_item。紐づけがなければ`None`(導入前に作成された既存キーが該当)。
  - `delete_api_key_owner(api_key_id: str) -> None`: ベストエフォート削除(キー削除・Bot公開解除のクリーンアップ用。失敗してもAPIキー削除自体は止めない)。
- `usecases/publication.py`:
  - `create_new_api_key`: `create_api_key(...)`直後に`bind_api_key_owner(key.id, user.id)`を呼ぶ。失敗した場合は作成済みAPIキーを削除してから例外を再送出し、紐づけのないキーが残らないようにする。
  - `remove_api_key`・`remove_bot_publication`: 既存の`delete_api_key`呼び出しと合わせて`delete_api_key_owner`も呼ぶ。

### 3. 公開APIリクエスト時のレートリミット判定・記録

- `routes/published_api.py`の`post_message()`(SQS投入前)で:
  1. `x-amzn-request-context`ヘッダーをパースし`identity.apiKeyId`を取得(ヘッダー欠如・パース失敗時は「紐づけなし」として扱う)。
  2. `find_api_key_owner(api_key_id)`でowner user_idを取得。
  3. 紐づけがあれば、既存の`check_rate_limit()`をそのowner user_idで呼ぶ(閾値・窓・キャッシュ等の判定ロジックは完全に既存のものを流用。シグネチャを`check_rate_limit(user: User)`から`check_rate_limit(user_id: str)`に変更 — 中身が`user.id`しか使っていなかったため)。超過時は既存の`RateLimitExceededError`→429ハンドラがそのまま機能する。
  4. 紐づけがなければ(導入前の既存キー)判定をスキップし、警告ログのみ出して通す(合意済みの互換性方針)。
  5. SQSに送るペイロード(`ChatInput`)に`rate_limit_user_id`(内部専用フィールド、外部公開スキーマ`ChatInputWithoutBotId`には含まれない)としてowner user_idを積む。
- `sqs_consumer.py`: `User.from_published_api_id(bot_id, billing_user_id=chat_input.rate_limit_user_id)`で、コスト記録先を紐づけユーザーにできるようにする。
- `app/user.py`: `User`に`billing_user_id: str | None = None`と`rate_limit_id`プロパティ(`billing_user_id or id`)を追加。
- `usecases/chat.py`の`post_process_result`: `record_usage(user.id, ...)` → `record_usage(user.rate_limit_id, ...)`。紐づけがある場合は紐づけユーザーのUsageLedgerTableに、それ以外(通常チャット・紐づけなし公開API)は従来通り`user.id`に記録される。

これにより、公開API経由のコストと通常チャットのコストが同じユーザーの同じ台帳に合算され、既存の5時間$10/7日間$336の閾値判定にそのまま乗る。

### 4. CDK配線

- `ApiPublishmentStackProps`に`usageLedgerTableName`, `apiKeyOwnerTableName`, `envPrefix`を追加。
- `bedrock-chat-stack.ts`で`database.usageLedgerTable.tableName`・`database.apiKeyOwnerTable.tableName`を(既存の`ConversationTableNameV3`等と同じパターンで)exportし、`cdk/bin/api-publish.ts`で`Fn.importValue`しCDK propsとして渡す。
- レートリミットSSMパラメータ名(`/{envPrefix}/rate-limit/five-hour-usd-limit`等)は文字列組み立てのみで決まる(パラメータARNもリージョン・アカウントから決定的に導出可能)ため、cross-stack exportは追加しない。パラメータ名生成ロジックを`cdk/lib/utils/parameter-models.ts`に共通関数として切り出し、`bedrock-chat-stack.ts`と`api-publishment-stack.ts`の両方から呼ぶ(重複回避)。
- `api-publishment-stack.ts`の`handlerRole`(apiHandler・sqsConsumeHandler共有)に、レートリミットSSMパラメータ2件への`ssm:GetParameter`権限を追加(ARNはリージョン・アカウント・パラメータ名から`Arn.format`で組み立て)。
- 環境変数(`USAGE_LEDGER_TABLE_NAME`, `RATE_LIMIT_FIVE_HOUR_PARAM_NAME`, `RATE_LIMIT_SEVEN_DAY_PARAM_NAME`, `API_KEY_OWNER_TABLE_NAME`)は、apiHandler・sqsConsumeHandlerの両方の`environment`ブロックに追加する(実際に使うのはapiHandlerが4つ全部、sqsConsumeHandlerは`USAGE_LEDGER_TABLE_NAME`のみだが、既存の2ハンドラの環境変数ブロックは元々ほぼ対称になっており、それに合わせて対称に追加する)。

## エラーハンドリング

- `RateLimitExceededError`・429ハンドラは既存のものをそのまま使う(`main.py`の例外ハンドラ登録はpublished API/通常APIで共通のコードパスのため追加作業不要)。
- `bind_api_key_owner`の書き込み失敗時: 作成済みAPIキーを削除してから例外を再送出(fail loud)。
- `x-amzn-request-context`ヘッダーが欠如・パース失敗する場合(ローカル開発環境、または想定外の実行環境): 「紐づけなし」として扱い、警告ログを出して処理は継続する(既存の`is_running_on_lambda()`ガードパターンと同様、ローカル開発を妨げない)。

## スコープ外

- 導入前に作成済み(紐づけ情報を持たない)既存APIキーへの遡及的な紐づけ強制・移行処理(合意済み: レート制限対象外のまま動作し続ける)。
- APIキー単体への独立した追加の利用上限(SSMパラメータ追加等)。今回はユーザー単位の既存閾値への合算のみ。
- `routes/published_api.py`の`GET /conversation/*`系エンドポイント(Bedrock課金を伴わないため判定不要)。
- フロントエンド(`BotApiSettingsPage.tsx`)側の表示変更(誰が作成したキーかの表示等)。
- `ApiPublishmentStack`自体を対象にした新規CDK自動テストの追加(前回のレートリミット機能追加時も同様の理由でルートスタックのテストのみ追加する方針を踏襲)。

## テスト方針

- `backend/app/repositories/api_key_owner.py`: 既存の`test_usage_limit.py`と同じ方式(`unittest.TestCase` + `patch("boto3.resource")` + `MagicMock`)で単体テスト。`bind_api_key_owner`の書き込み内容、`find_api_key_owner`の紐づけあり/なし、`delete_api_key_owner`が例外を飲み込むことを検証する。
- `usecases/publication.py`: `create_new_api_key`が紐づけ成功時に正しいuser_idで`bind_api_key_owner`を呼ぶこと、紐づけ失敗時にAPIキーを削除してから例外を再送出すること、`remove_api_key`/`remove_bot_publication`が`delete_api_key_owner`も呼ぶことを検証する。
- `usecases/rate_limit.py`: シグネチャ変更(`user: User` → `user_id: str`)に合わせて既存テストを更新するのみ(判定ロジック自体に変更はない)。
- `app/user.py`: `rate_limit_id`プロパティ(紐づけあり/なしの両方)を検証する。
- `routes/published_api.py`のヘッダー解析ヘルパー: 正常なJSON、ヘッダー欠如、不正なJSONの3パターンを検証する。
- CDK: `cdk/test/cdk.test.ts`のルートスタックテストに、新規`ApiKeyOwnerTable`リソースと新規exportの存在を検証するアサーションを追加する。`api-publishment-stack.ts`・`api-publish.ts`自体への新規テストハーネスは追加しない(前回設計と同じ方針)。

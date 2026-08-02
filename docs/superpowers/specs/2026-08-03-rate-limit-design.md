# ユーザー毎コストレートリミット 設計書

## 背景・目的

ユーザー毎に、直近の利用コスト(Bedrock課金)が一定額を超えた場合にチャットを行えなくする。

- 過去5時間の累計コストが $10 を超えたらチャットをブロックする
- 過去7日間の累計コストが $336 を超えたらチャットをブロックする
- 上記2つの閾値($10 / $336)はコード変更・再デプロイなしで変更できるようにする

対象は通常のチャットUI経由の利用(WebSocketストリーミング / REST の `POST /conversation`)のみ。公開bot API(`published_api.py` / `sqs_consumer.py` 経由)は対象外とする。管理者(Admin)グループのユーザーも例外とせず、全ユーザーに適用する。

## 前提・既存実装の確認結果

- 1メッセージあたりのコストは `calculate_price()`(`backend/app/bedrock.py`)で算出済みで、`post_process_result()`(`backend/app/usecases/chat.py`)内で `message.price` および `conversation.total_price`(会話単位の累計)としてDynamoDBの `conversationTable` に保存されている。
- `conversationTable` の1アイテムは1会話全体であり、`CreateTime` は会話作成時刻のみを持つ。会話が長期間続く場合、`TotalPrice` を `CreateTime` で時間窓に按分することはできない(不正確になる)。**ユーザー単位で「直近N時間の利用額」を安価に集計できる仕組みは現状存在しない**。
- 既存の `usage_analysis.py`(Athena経由のユーザー別コスト集計)は、DynamoDB→S3のエクスポートが5分間隔のバッチ処理であり、リアルタイムのブロック判定用途には遅延が大きく不向き。管理画面用の集計機能であり、本機能とは別物として扱う。
- 設定値(閾値)は、このリポジトリではこれまで全てCDKデプロイ時に環境変数へ焼き込む方式で管理されており、SSM Parameter Storeの利用例は存在しない。今回は「再デプロイ不要で変更したい」という要件があるため、新規にSSM Parameter Storeを導入する。
- 通常UI経路(`websocket.py`, `routes/conversation.py`)と公開API経路(`published_api.py`, `sqs_consumer.py`)は共通の `chat()`(`backend/app/usecases/chat.py`)を呼び出す。公開API側の `user_id` は `User.from_published_api_id()` により `PUBLISHED_API#{bot_id}` という別名前空間になっており、通常ユーザーのIDとは衝突しない。

## アーキテクチャ

### 使用量記録: 新規DynamoDBテーブル `UsageLedgerTable`

- `cdk/lib/constructs/database.ts` に追加。既存 `conversationTable` と同じ行レベルセキュリティ(`tableAccessRole` によるセッションポリジー方式、`LeadingKeys` 条件)をそのまま利用する。
- キー設計:
  - PK: `UserId` (string)
  - SK: `Timestamp` (number, epoch microseconds。同一ユーザーの連続リクエストでの衝突を避けるためマイクロ秒精度とする)
- 属性:
  - `Price` (Decimal): そのメッセージ生成にかかったコスト
  - `expire` (number, TTL属性): 記録時刻 + 8日(7日間の判定窓 + 予備1日)。DynamoDB TTLによる自動削除に任せ、クリーンアップ処理は別途実装しない。
- `BillingMode.PAY_PER_REQUEST`、`tableAccessRole.grantReadWriteData(usageLedgerTable)` を追加。

### 使用量の記録タイミング

- `post_process_result()`(`backend/app/usecases/chat.py`)内、`conversation.total_price += result["price"]` の直後に1回、新規リポジトリ関数を呼び出しレコードを書き込む。
- `chat()` は通常UI・公開API・SQS経由のいずれからも共通で呼ばれる関数だが、公開API分の記録は前述の通り別名前空間(`PUBLISHED_API#{bot_id}`)に入るため、書き込み処理自体を経路によって分岐させる必要はない(分岐させない方がシンプル)。

### 新規リポジトリ `backend/app/repositories/usage_limit.py`

- `record_usage(user_id: str, price: float, now: float) -> None`: `UsageLedgerTable` に1件 `put_item`。
- `get_usage_since(user_id: str, since: float) -> float`: `Key("PK").eq(user_id) & Key("SK").gte(since_micros)` でQueryし、取得した `Price` をPython側でSUMして返す(DynamoDBはサーバー側SUM集計に対応しないため)。ページネーション(`LastEvaluatedKey`)は既存の `find_conversation_by_user_id` と同様に処理する。

### 閾値設定: SSM Parameter Store

- 新規パラメータを2つ、`StringParameter`(型: String、値は数値の文字列表現。例 `"10"`)としてCDKで作成する。パラメータ名は次の通り(`{envPrefix}` は既存の環境分離プレフィックスをそのまま用いる):
  - `/{envPrefix}/rate-limit/five-hour-usd-limit`(デフォルト `"10"`)
  - `/{envPrefix}/rate-limit/seven-day-usd-limit`(デフォルト `"336"`)
- 運用者は `aws ssm put-parameter --overwrite` で再デプロイ不要に値を変更できる。Lambda側はパラメータ名を環境変数(`RATE_LIMIT_FIVE_HOUR_PARAM_NAME` / `RATE_LIMIT_SEVEN_DAY_PARAM_NAME`)経由で受け取り、値を `float()` に変換して使用する。
- Lambda(REST用 `HandlerV2`、WebSocket用ハンドラの両方)の実行ロールに対象パラメータへの `ssm:GetParameter` を付与する。
- Lambda側では取得値をプロセス内メモリに60秒程度キャッシュし、チャット毎のSSM呼び出しコスト・レイテンシ・スロットリングリスクを抑える(値変更が反映されるまで最大60秒程度のタイムラグは許容する)。

### 判定ロジック `check_rate_limit(user: User) -> None`

- `now = 現在時刻`
- `five_hour_sum = get_usage_since(user.id, now - 5*3600)`
- `seven_day_sum = get_usage_since(user.id, now - 7*24*3600)`
- `five_hour_sum > five_hour_limit または seven_day_sum > seven_day_limit` の場合(超過、`>=` ではなく `>`)、`RateLimitExceededError` を送出する。
- 呼び出し箇所は次の2箇所のみ、いずれも `chat()` 呼び出しの**直前**(Bedrock呼び出し前にブロックし、無駄な課金を発生させない):
  - `backend/app/routes/conversation.py` の `post_message()`
  - `backend/app/websocket.py` の `process_chat_input()`
- `chat()` 本体・`published_api.py` ・`sqs_consumer.py` には変更を加えない(公開APIは対象外のため)。

### エラーハンドリング

- `RateLimitExceededError` を `backend/app/repositories/common.py` の既存例外群(`RecordNotFoundError` 等)に倣って追加。
- REST: `backend/app/main.py` に既存パターンと同様 `app.add_exception_handler(RateLimitExceededError, error_handler_factory(429))` を追加するのみ。ルート側(`routes/conversation.py`)は他のエラーと同じく素通しでよい。
- WebSocket: `backend/app/websocket.py` の `process_chat_input()` にある既存の `except RecordNotFoundError:` と同じパターンで `except RateLimitExceededError:` を追加し、`statusCode: 429` のエラーフレーム(`status: "ERROR"`, `reason: <メッセージ>`)を返す。
- フロントエンド: エラー受信時にユーザーへ分かりやすいメッセージを表示する。直近の `per-chat-cost-display` ブランチのi18n追加パターン(`frontend/src/i18n/en/index.ts` / `ja/index.ts` のみに追加、他言語は `fallbackLng` で英語にフォールバック)を踏襲する。

## スコープ外

- 公開bot API(`published_api.py` / `sqs_consumer.py`)経由の利用へのレートリミット適用
- 閾値超過が近づいていることを事前に警告するUI(閾値到達時にブロックするのみ)
- ストリーミング中の応答生成コストを、生成途中でリアルタイムに打ち切る仕組み(コストはBedrock呼び出し完了後にしか確定しないため、次回以降のリクエストをブロックする形になる。これは本設計の構造上の制約であり、対応しない)
- 同時多発リクエストに対する強整合性のロック(check-then-actのため、ごく短時間・僅かな超過を許容するベストエフォート方式とする)

## テスト方針

- `backend/app/repositories/usage_limit.py`: moto使用のDynamoDB単体テスト。`record_usage` の書き込み内容、`get_usage_since` の時間窓境界値(窓のちょうど境界、境界の前後)を検証する。
- レートリミット判定ロジック: 閾値ちょうど・閾値超過・閾値未満の3パターンで `RateLimitExceededError` が送出される/されないことを検証する(`>` であって `>=` でないことを明示的にテストする)。
- 既存の `test_chat.py` 等が、新規の記録処理追加によって壊れないことを確認する。

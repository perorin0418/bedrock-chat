# Claude Code (Bedrock直接アクセス) コスト統合 設計書

## 1. 背景・目的

社員がAWS Bedrockを介してClaude Code CLIを直接利用する際のコストが無尽蔵に膨らまないよう、Bedrock Chatアプリが既に持つ「ユーザー別5時間/7日間ローリングウィンドウ・レート制限」に、Claude Code分の利用コストを統合する。

Claude Code(IAM直接アクセス)はBedrock Chatのバックエンドを経由しないため、既存のリアルタイム制御(`check_rate_limit`)がそのままは効かない。本機能は、Cognitoユーザー作成と同時に専用IAMユーザーを発行し、AWS Cost and Usage Report (CUR) 2.0経由でそのIAMユーザーのBedrock利用コストを収集して既存の利用台帳に合流させ、閾値超過時にIAMユーザーへDenyポリシーを自動付与することで、この隙間を埋める。

## 2. 前提・既存実装の確認結果

以下は実装前に実際にコードを読んで確認した事実。

| ファイル | 確認内容 |
|---|---|
| `backend/app/repositories/usage_limit.py` | `record_usage(user_id, price)` がDynamoDB(`USAGE_LEDGER_TABLE_NAME`)に `PK=user_id`(String), `SK=epoch ms`(Number), `Price`(Decimal), `expire`(TTL, 8日)で書き込み。`get_usage_since(user_id, since_ms)` が該当期間のPrice合計を返す |
| `backend/app/usecases/rate_limit.py` | `check_rate_limit(user_id)` がSSMパラメータ(`RATE_LIMIT_FIVE_HOUR_PARAM_NAME`/`RATE_LIMIT_SEVEN_DAY_PARAM_NAME`, 60秒キャッシュ)と`get_usage_since`の値を厳密`>`で比較し、超過時`RateLimitExceededError`を送出 |
| `backend/auth/add_user_to_groups/add_user_to_groups.py` | Cognito `PostConfirmation_ConfirmSignUp`/`PostAuthentication_Authentication`トリガー(`cdk/custom-resources/cognito-trigger`経由のCustomResourceで後付け登録、1トリガーにつきLambda1つのみ)。`app.*`パッケージ非依存のスタンドアロンLambda |
| `cdk/lib/constructs/database.ts` | `usageLedgerTable`(PK=`PK`, SK=`SK`)、`tableAccessRole`(STS AssumeRole + `LeadingKeys`セッションポリシーによる行レベルセキュリティ) |
| `cdk/lib/constructs/usage-analysis.ts` | 定期実行Lambda(`events.Rule` + `Schedule.cron`)+ Glue/Athenaの既存パターン。ただし対象はDynamoDBのPITR ExportでありCURとは無関係 |
| `cdk/lib/api-publishment-stack.ts` / `cdk/bin/api-publish.ts` | メインスタックと独立したCDKアプリ+スタック。`Fn.importValue`でメインスタックのテーブル名等をimportし、SSMパラメータ名は`rateLimitParamName()`を両スタックで呼んで決定的に再導出(exportしない) |

### AWSアカウント構成に関する調査結果(重要な設計判断の根拠)

AWS公式ドキュメント(`docs.aws.amazon.com/cur/latest/userguide/dataexports-organizations.html`等)を確認した結果:

- CUR 2.0 (Data Exports) は**Organizationsの管理アカウントに限らずメンバーアカウント単体でも作成可能**で、IAMポリシーの要件は管理/メンバーで同一
- メンバーアカウントが作成するCURには**そのアカウント自身のコスト・使用量データのみ**が含まれる
- CURの作成アカウントは**配信先S3バケットを自身で所有している必要があり、クロスアカウント配信は不可**
- Bedrock呼び出し時のIAMプリンシパル識別(`line_item_iam_principal`)は**自動記録**されており、有効化操作は不要。CUR 2.0 Data Export作成時に「Include caller identity (IAM principal) allocation data」を選択するだけでよい
- `AWS::BCMDataExports::Export` というCloudFormationリソースが存在し、**CUR 2.0 Export自体をCDKで宣言的に作成できる**
  - **追記(実機デプロイで発覚)**: このリソースタイプは**us-east-1にしか登録されていない**(`bcm-data-exports`サービス自体がus-east-1にしかAPIエンドポイントを持たないため)。ap-northeast-1等にデプロイすると`Template format error: Unrecognized resource types: [AWS::BCMDataExports::Export]`で失敗する。対応として、ネイティブCFNリソースの代わりにLambdaバックエンドのカスタムリソース(`cdk/custom-resources/claude-code-cur-export/`)を使い、Lambda内のboto3クライアントで明示的に`region_name="us-east-1"`を指定する方式に変更した(セクション3参照)

Claude Code用IAMユーザーはBedrock Chatと同一アカウントに作成するため、このアカウント自身でCUR 2.0を作成すれば、管理アカウントとの調整もクロスアカウントのS3バケット共有も一切不要となる。これにより、当初想定していた「管理アカウント側の手動前提条件」は不要になった。

## 3. アーキテクチャ

```
[Cognito PostConfirmation_ConfirmSignUp]
  └─ add_user_to_groups.py (既存Lambda。IAMプロビジョニング処理を追加)
       ├─ IAMユーザー "claude-code-{sub}" 作成 + ClaudeCodeBedrockAccessPolicyアタッチ(冪等)
       ├─ アクセスキー発行(既存アクティブキーがあればスキップ)
       ├─ Secrets Manager "claude-code/{sub}" へ保存
       ├─ ClaudeCodeIamUserTable(新規)へ状態記録
       └─ 失敗時: SNS通知(fail-open、Cognitoサインアップ自体は常に成功)

[CUR 2.0 Data Export(同一アカウント、CDK管理)]
  └─ Glue Crawler(日次)がParquetデータをカタログ化

[EventBridge Rule (1時間毎)]
  └─ claude_code_cost_sync Lambda(新規、backend/claude_code_cost_sync/)
       ├─ ClaudeCodeCostSyncRoleをAssumeRole(usageLedgerTable書き込み+IAM Deny権限)
       ├─ Athenaで直近8日分をline_item_iam_principal別に集計(claude-code-*のみ)
       ├─ usageLedgerTableへ「ユーザー×日」の冪等な洗い替えPutItem
       ├─ ClaudeCodeIamUserTableを走査し、check_rate_limitと同一ロジックで5h/7d合算値を評価
       ├─ 超過→対象IAMユーザーにDeny inlineポリシー付与(新規発生時はSNS通知)/ 未達→解除
       └─ クエリ失敗時: フェイルオープン(状態維持)+ SNS通知
```

### 新規CDKリソース(メインスタック、`cdk/lib/constructs/database.ts`)

- `ClaudeCodeIamUserTable`(PK: `UserId`) — IAMユーザー発行状態・Deny状態を記録
- `ClaudeCodeCostSyncRole` — 別スタックの同期LambdaがAssumeRoleする専用ロール。`usageLedgerTable.grantWriteData()` + `ClaudeCodeIamUserTable.grantReadWriteData()` + `iam:PutUserPolicy/DeleteUserPolicy`を`user/claude-code-*`にスコープ。既存の`tableAccessRole`(ユーザー単位の行レベルセキュリティ用)とは別に新設した理由は、同期Lambdaが多数ユーザー分を1回の実行でまとめて書く信頼されたバッチ処理であり、`LeadingKeys`によるユーザー単位のセッションポリシー制限が不要かつ非効率なため

### 新規CDKリソース(メインスタック、`cdk/lib/constructs/auth.ts`)

- `ClaudeCodeBedrockAccessPolicy` — 社員のIAMユーザーにアタッチするBedrock呼び出し許可ポリシー
- `ClaudeCodeNotificationTopic`(SNS) — IAMプロビジョニング失敗/コスト同期失敗/新規Deny発生の通知先。常時作成(アイドルコストがほぼ無いため条件分岐を避けて単純化)。オプションでメール購読可能(`claudeCodeNotificationEmail`パラメータ)
- `addUserToGroupsFunction`への追加権限: IAM(`CreateUser`/`GetUser`/`ListAccessKeys`/`CreateAccessKey`/`DeleteAccessKey`/`AttachUserPolicy`、すべて`user/claude-code-*`スコープ)、Secrets Manager(`claude-code/*`スコープ)、`ClaudeCodeIamUserTable`読み書き、SNS Publish
- Cognitoトリガー登録条件に`enableClaudeCodeProvisioning`を追加(既存は`autoJoinUserGroups`/`requireAdminApproval`のみだった)

### 新規独立CDKアプリ(`ApiPublishmentStack`と同パターン)

- `cdk/bin/claude-code-cost-sync.ts` / `cdk/lib/claude-code-cost-sync-stack.ts` — IAM操作という強い権限を持つため、メインスタックとは別のデプロイライフサイクル・権限境界として独立させた
- `cdk/lib/constructs/claude-code-cur-analysis.ts`:
  - CUR出力先の新規S3バケット(同一アカウント所有)
  - CUR 2.0 Data Exportの作成は、Lambdaバックエンドのカスタムリソース(`cdk/custom-resources/claude-code-cur-export/`、`cognito-trigger`と同じ実装パターン)経由で行う。`bcm-data-exports`のCloudFormationリソースタイプ・APIエンドポイントがus-east-1にしか存在しないため、Lambda内のboto3クライアントで`region_name="us-east-1"`を明示指定することでスタック自体のリージョンに関係なくデプロイ可能にしている。テーブル`COST_AND_USAGE_REPORT`、`INCLUDE_IAM_PRINCIPAL_DATA: "TRUE"`、`TIME_GRANULARITY: "DAILY"`、Parquet形式、`OVERWRITE_REPORT`。S3バケットポリシーの`aws:SourceArn`条件も同様の理由でus-east-1を固定値として使用
  - Glue Database + **Glue Crawler**(日次スケジュール)。CUR 2.0のParquetスキーマは125以上の列を持ち複数のネスト列を含むため、AWS公式ドキュメントが強く推奨する「Glueクローラーでスキーマを自動検出する」方式を採用し、`usage-analysis.ts`のような手動スキーマ宣言はしていない。クローラーが割り当てるテーブル名はsynth時点で確定しないため、同期Lambda側で`glue:GetTables`により実行時に動的発見する設計とした
  - Athena Workgroup + クエリ結果用バケット

## 4. `add_user_to_groups.py` の変更

`PostConfirmation_ConfirmSignUp`分岐に`ENABLE_CLAUDE_CODE_PROVISIONING`が真の場合の処理を追加。`user_id`は`event["userName"]`ではなく**`user_attributes["sub"]`**(アプリ全体で使われる`user_id`と一致させるため)。

新規関数: `_iam_user_name`, `_secret_name`, `_ensure_iam_user`, `_ensure_access_key`, `_store_access_key_secret`, `_record_provisioning`, `_notify`, `provision_claude_code_iam_user`(オーケストレーション)。

**エラーハンドリング方針**: Cognitoサインアップ自体は常に成功させる(`provision_claude_code_iam_user`全体をtry/exceptで包み例外を再送出しない)。内部処理は冪等(`EntityAlreadyExistsException`握りつぶし、既存アクティブキーがあれば`create_access_key`をスキップ)。Secrets Manager書き込み失敗時のみ、直前に作成したアクセスキーを`delete_access_key`でロールバック(シークレット値は再取得不能なため、ゴミが残るとIAMの2キー上限で次回失敗するのを防ぐ)。失敗時はSNS通知(best-effort、通知自体の失敗も握りつぶす)。

## 5. `claude_code_cost_sync` Lambda(新規, `backend/claude_code_cost_sync/`)

- `app.*`パッケージに依存しないスタンドアロン構成(既存の`s3_exporter`/`add_user_to_groups`と同じ方針)
- Athenaクエリ: `line_item_iam_principal LIKE '%:user/claude-code-%'` かつ `line_item_line_item_type = 'Usage'` で直近8日分(7日ローリングウィンドウ+CUR後方修正バッファ1日)を「IAMプリンシパル×日」でGROUP BY
- `usageLedgerTable`への書き込みは**冪等な洗い替え**: `SK`をその日のUTC0時epoch msに固定してPutItem(watermark方式ではなく毎回8日分を上書き。CURの後方修正・バージョニング方式に依存しない設計)
- Deny判定ロジックは`backend/app/usecases/rate_limit.py`の`check_rate_limit`と**同一の計算式を意図的に複製**(このLambdaが`app.*`非依存のため)。SSMパラメータ名のみ環境変数で共通化し、閾値の出どころは一致させている
- Deny付与/解除: `get_user_policy`で現状確認→必要な時のみ`put_user_policy`/`delete_user_policy`(固定ポリシー名`ClaudeCodeRateLimitDeny`、対象アクションは`bedrock:InvokeModel*`/`Converse*`)
- `usageLedgerTable`と`ClaudeCodeIamUserTable`への読み書き、およびDeny付与/解除のIAM操作は、`ClaudeCodeCostSyncRole`をAssumeRoleしたセッションで実行(Athena/Glue/S3操作はこのLambda自身の実行ロールで直接実行)

## 6. エラーハンドリング

| 事象 | 挙動 |
|---|---|
| IAMプロビジョニング失敗(`add_user_to_groups.py`) | Cognitoサインアップは成功させる。ログ+SNS通知のみ |
| Athenaクエリ失敗 | フェイルオープン(台帳・Deny状態を変更せず、次回サイクルに委ねる)。SNS通知の上で例外を再送出(Lambda呼び出し自体は失敗として記録される) |
| 個別ユーザーのDeny評価失敗 | そのユーザーのみスキップし、他ユーザーの評価は継続(try/exceptで隔離) |
| 新規Deny発生 | SNS通知(既にDeny中のユーザーへの再付与は通知しない) |

## 7. スコープ外(今回は実装しない)

- **オフボーディング**: Cognitoユーザー削除フック、退職時のIAMユーザー・アクセスキー削除。本番コードにCognitoユーザー削除フローが現状存在しないため、別タスクとする
- CloudWatch Alarm/Metric Filterによる監視(SNS直接publishで代替。理由はセクション5参照)
- アクセスキーの自動ローテーション

## 8. テスト方針

- 本リポジトリは`moto`未使用。DynamoDB/IAM/SecretsManager/STS/SNS/Athena/Glueは全て`unittest.TestCase` + `patch("boto3.client"/"boto3.resource")` + `MagicMock`でモック
- `add_user_to_groups.py`・`claude_code_cost_sync`は既存スタンドアロンLambda(`s3_exporter`, 元の`add_user_to_groups`)にテストが一切ない慣習から意図的に逸脱し、新規にテストを追加(IAM/Secrets Manager副作用を伴う重要ロジックのため)
- `provision_claude_code_iam_user`は「Secrets Manager書き込み失敗時に`delete_access_key`でロールバックし、かつ例外を外に伝播させない」ことを明示的にテスト
- CDK: `cdk/test/cdk.test.ts`に新規テーブル・IAMポリシー(Resource ARNが`user/claude-code-*`にスコープされていること)・CfnOutputの存在検証を追加。`ClaudeCodeCostSyncStack`自体は`ApiPublishmentStack`と同じ方針で専用の自動テストハーネスは追加せず、代わりに独立アプリとして`cdk synth`が通ることを実装時に確認した

# Claude Code (Bedrock直接アクセス) コスト統合 実装記録

For agentic workers: この機能をさらに拡張する場合は、`docs/superpowers/specs/2026-08-03-claude-code-cost-sync-design.md`を先に読むこと。

**Goal:** Bedrock Chat既存のユーザー別5時間/7日間レート制限に、Claude Code CLI(Bedrock直接アクセス)の利用コストを統合する。
**Architecture:** Cognitoサインアップ時のIAMユーザー自動発行 + CUR 2.0(同一アカウント、CDK管理)+ Athena + 1時間毎の同期LambdaによるDeny自動付与/解除。
**Tech Stack:** AWS CDK(TypeScript)、Python(標準boto3、`app.*`パッケージ非依存のスタンドアロンLambda)。

**本ドキュメントは実装完了後に書き起こした記録**であり、`2026-08-03-published-api-rate-limit.md`のような実装前のTDD計画書ではない。各Taskは実装済み・テスト通過済み。今後同種の機能を追加する際の参考にすること。

## Global Constraints

- 本リポジトリは`moto`未使用。DynamoDB/IAM/SecretsManager/STS/SNS/Athena/Glueのテストは全て`unittest.TestCase` + `patch("boto3.client"/"boto3.resource")` + `MagicMock`
- `backend/auth/add_user_to_groups/`、`backend/claude_code_cost_sync/`はいずれも`app.*`パッケージに依存しないスタンドアロンLambda(`backend/s3_exporter/`と同じ方針)。テストファイルは`sys.path.insert(0, "<lambdaディレクトリ>")`してから`import <モジュール名>`する
- CDKの`cdk/test/cdk.test.ts`はPythonバンドリングを含むため1テストケースあたり約200秒かかる。実行は`npx jest cdk.test.ts -t "<テスト名>"`で個別に絞る
- 独立CDKアプリ(`ClaudeCodeCostSyncStack`)は`npx cdk synth --app "npx ts-node --prefer-ts-exts bin/claude-code-cost-sync.ts"`で単独synth検証する。メインの`cdk.out`と衝突する場合は`--output <別ディレクトリ>`を指定する

## Task 1: CDKにClaudeCodeIamUserTable / ClaudeCodeCostSyncRoleを追加 [完了]

**Files:**
- Modify: `cdk/lib/constructs/database.ts`
- Modify: `cdk/lib/bedrock-chat-stack.ts`(CfnOutput export追加)
- Modify: `cdk/test/cdk.test.ts`

**Interfaces:**
- `Database.claudeCodeIamUserTable: Table`(PK: `UserId`)
- `Database.claudeCodeCostSyncRole: Role`(`usageLedgerTable.grantWriteData()` + `claudeCodeIamUserTable.grantReadWriteData()` + `iam:PutUserPolicy/DeleteUserPolicy/GetUserPolicy`を`user/claude-code-*`にスコープ)
- Export名: `${envPrefix}-BedrockClaudeChatClaudeCodeIamUserTableName`, `${envPrefix}-BedrockClaudeChatClaudeCodeCostSyncRoleArn`(Task 8で独立スタックがimport)

**検証**: `npx jest cdk.test.ts -t "default stack"` 通過(新規テーブル・IAMポリシーResourceスコープ・CfnOutputのアサーション含む)

## Task 2: ClaudeCodeBedrockAccessPolicy追加 + addUserToGroupsFunctionへの権限配線 [完了]

**Files:**
- Modify: `cdk/lib/constructs/auth.ts`

**Interfaces:**
- `ClaudeCodeBedrockAccessPolicy`(ManagedPolicy): `bedrock:InvokeModel*`/`Converse*`を`Resource: "*"`で許可、社員IAMユーザーにアタッチする
- 環境変数追加: `ENABLE_CLAUDE_CODE_PROVISIONING`(デフォルト`false`)、`CLAUDE_CODE_IAM_USER_TABLE_NAME`、`CLAUDE_CODE_BEDROCK_POLICY_ARN`
- Cognitoトリガー登録条件に`props.enableClaudeCodeProvisioning`を追加(既存の`autoJoinUserGroups.length >= 1 || requireAdminApproval`のみだった条件を拡張)
- **CDK設計上の注意**: `database`の生成を`auth`より前に移動する必要があった(`Auth`が`database.claudeCodeIamUserTable`を必要とするため。`bedrock-chat-stack.ts`内の宣言順序を入れ替えた)

**検証**: `npx jest cdk.test.ts -t "default stack"`, `-t "Identity Provider Generation"` 通過

## Task 3: add_user_to_groups.pyにIAMプロビジョニングロジック追加 [完了]

**Files:**
- Modify: `backend/auth/add_user_to_groups/add_user_to_groups.py`
- Create: `backend/tests/test_auth/test_add_user_to_groups.py`

**Interfaces:**
- `provision_claude_code_iam_user(user_id, cognito_username)`: オーケストレーション。例外を外に伝播させない(fail-open)
- `_iam_user_name(user_id) -> str`: `f"claude-code-{user_id}"`
- `_ensure_iam_user`, `_ensure_access_key`, `_store_access_key_secret`, `_record_provisioning`: 冪等な各ステップ

**重要な設計判断**: `user_id`は`event["userName"]`ではなく`event["request"]["userAttributes"]["sub"]`から取得する必要がある(アプリ全体の`user_id`と一致させるため)。

**検証**: `python -m pytest tests/test_auth/test_add_user_to_groups.py -v`(19テスト通過。SNS通知テスト含む、Task 9参照)

## Task 4: parameter-models.tsにresolveClaudeCodeCostSyncParameters追加 [完了]

**Files:**
- Modify: `cdk/lib/utils/parameter-models.ts`

**Interfaces:**
- `ClaudeCodeCostSyncParametersSchema`(`claudeCodeCostSyncNotificationEmail`のみ。CURは同一アカウント内のため、当初想定していたCURバケット名/アカウントIDのパラメータは不要になった)
- `resolveClaudeCodeCostSyncParameters(app: App)`: メインスタックの`resolveBedrockChatParameters`と同じcdk.jsonコンテキストベース(`resolveApiPublishParameters`のCodeBuild向けenv varベースとは異なる。理由: このスタックはCodeBuildで動的デプロイされるのではなく、メインスタックと同様に`cdk deploy`される)

**検証**: `npx tsc --noEmit -p .`

## Task 5: CDK: CUR 2.0 Export + Glue/Athena construct新規作成 [完了]

**Files:**
- Create: `cdk/lib/constructs/claude-code-cur-analysis.ts`
- Create: `cdk/custom-resources/claude-code-cur-export/index.py`

**Interfaces:**
- `ClaudeCodeCurAnalysis.database: glue.IDatabase`
- `ClaudeCodeCurAnalysis.curBucket`, `resultOutputBucket: s3.IBucket`
- `ClaudeCodeCurAnalysis.workgroupName`, `workgroupArn: string`
- **テーブル名は公開しない**: Glue Crawlerが動的に命名するため、synth時点で確定しない。呼び出し側(Task 6)は`glue:GetTables`で実行時に発見する

**重要な設計判断**: CUR 2.0のParquetスキーマ(125以上の列、複数ネスト列)についてAWS公式ドキュメントが「Glueクローラーでの自動検出」を強く推奨しているため、`usage-analysis.ts`(DynamoDB Export用、手動スキーマ宣言)とは異なりGlue Crawlerベースの設計を採用した。

**追記(実機デプロイで発覚した不具合と修正)**: 当初`aws-cdk-lib.aws_bcmdataexports.CfnExport`(ネイティブCFNリソース)を使っていたが、ap-northeast-1への実デプロイで`Template format error: Unrecognized resource types: [AWS::BCMDataExports::Export]`が発生した。原因は`bcm-data-exports`サービス自体がus-east-1にしかAPIエンドポイントを持たず、CloudFormationリソースタイプもus-east-1にしか登録されていないため。`cdk/custom-resources/cognito-trigger`と同じパターンでLambdaバックエンドのカスタムリソースに置き換え、Lambda内のboto3クライアントで`region_name="us-east-1"`を明示指定することで解決した(スタック自体はどのリージョンにデプロイしてもよい)。合わせてS3バケットポリシーの`aws:SourceArn`条件も`Stack.of(this).region`ではなく`us-east-1`固定に修正した。

**検証**: `npx tsc --noEmit -p .` + Task 8での`cdk synth`による統合検証

## Task 6 & 7: claude_code_cost_sync Lambda(Athenaクエリ+台帳書き込み+Deny判定/付与/解除) [完了]

**Files:**
- Create: `backend/claude_code_cost_sync/index.py`
- Create: `backend/claude_code_cost_sync/requirements.txt`
- Create: `backend/tests/test_claude_code_cost_sync/test_index.py`

**Interfaces:**
- `handler(event, context)`: `sts.assume_role`でClaudeCodeCostSyncRoleに切り替え→Athenaクエリ→台帳書き込み→Deny評価。クエリ失敗時はフェイルオープン(状態維持)+ SNS通知 + 例外再送出
- `_find_cur_table_name(database_name) -> str`: Glue Crawlerが命名したテーブルを実行時発見
- `_build_cost_query`, `_run_athena_query`, `_paginate_query_results`: Athenaクエリ実行(直近8日、`line_item_line_item_type = 'Usage'`、`line_item_iam_principal LIKE '%:user/claude-code-%'`)
- `_rows_to_ledger_items`, `_write_ledger_items`: 「ユーザー×日」単位の冪等な洗い替えPutItem(SK=UTC日付0時epoch ms)
- `get_usage_since`, `_get_limit`: `app/repositories/usage_limit.py`・`app/usecases/rate_limit.py`と同一ロジックを意図的に複製(スタンドアロンLambdaのため)
- `_is_denied`, `_apply_deny`, `_remove_deny`: 固定ポリシー名`ClaudeCodeRateLimitDeny`のDeny inlineポリシー冪等操作
- `_evaluate_and_apply_deny`: ユーザー単位でtry/except隔離、新規Deny発生時のみSNS通知

**検証**: `python -m pytest tests/test_claude_code_cost_sync/test_index.py -v`(29テスト通過)

## Task 8: CDK: claude-code-cost-sync-stack.ts / bin新規作成 [完了]

**Files:**
- Create: `cdk/lib/claude-code-cost-sync-stack.ts`
- Create: `cdk/bin/claude-code-cost-sync.ts`

**Interfaces:**
- `ClaudeCodeCostSyncStack`: `ApiPublishmentStack`と同じ独立CDKアプリパターン。`Fn.importValue`でメインスタックの`UsageLedgerTableName`/`ClaudeCodeIamUserTableName`/`ClaudeCodeCostSyncRoleArn`/`ClaudeCodeNotificationTopicArn`をimport
- SSMパラメータ名は`rateLimitParamName()`を再利用して両スタックで決定的に導出(exportしない、`ApiPublishmentStack`と同じ方針)
- `events.Schedule.rate(Duration.hours(1))`でLambdaを定期実行

**検証**: `npx cdk synth --app "npx ts-node --prefer-ts-exts bin/claude-code-cost-sync.ts" --output cdk.out.tmp` が成功し、`Custom::ClaudeCodeCurExport`(us-east-1固定のカスタムリソース)・`AWS::Glue::Crawler`・`AWS::Athena::WorkGroup`等が期待通り生成されることを確認済み

## Task 9: 通知(SNS)追加 [完了]

**Files:**
- Modify: `cdk/lib/constructs/auth.ts`(`ClaudeCodeNotificationTopic`新設、常時作成)
- Modify: `cdk/lib/bedrock-chat-stack.ts`(export追加、`claudeCodeNotificationEmail`プロパティ追加)
- Modify: `cdk/lib/utils/parameter-models.ts`, `cdk/bin/bedrock-chat.ts`, `cdk/cdk.json`(`claudeCodeNotificationEmail`パラメータ配線)
- Modify: `cdk/lib/claude-code-cost-sync-stack.ts`, `cdk/bin/claude-code-cost-sync.ts`(topic ARN import + `sns:Publish`権限)
- Modify: `backend/auth/add_user_to_groups/add_user_to_groups.py`(`_notify`ヘルパー、プロビジョニング失敗時に通知)
- Modify: `backend/claude_code_cost_sync/index.py`(`_notify`ヘルパー、クエリ失敗時・新規Deny発生時に通知)

**設計判断(CloudWatch Alarm/Metric Filterではなく直接SNS publish)**: 当初はCloudWatch Logs Metric Filter + Alarmでの実装を検討したが、(1) `add_user_to_groups.py`のプロビジョニング失敗はfail-openでLambda自体は正常終了するためLambda Errorsメトリクスでは検知できない、(2) 別スタックの2つのLambdaのログをまたぐMetric Filter設定はクロスススタックのLogGroup参照が必要で複雑、という理由から、各失敗パスで明示的に`sns.publish()`を呼ぶ設計に簡略化した。

**検証**: 両Lambdaのテストに`_notify`呼び出しのアサーションを追加(計49テスト通過)。`npx jest cdk.test.ts -t "default stack"`でSNSトピック追加後もメインスタックがsynth可能なことを確認済み

## Task 10: 本ドキュメント作成 [完了]

## スコープ外(未実装、将来の別タスク)

- **オフボーディング**: Cognitoユーザー削除フック、退職時のIAMユーザー・アクセスキー削除。本番コードにCognitoユーザー削除フローが現状存在しないため
- CloudWatch Alarm/Metric Filterベースの監視の追加(SNS直接publishで一次対応済み)
- アクセスキーの自動ローテーション
- Deny解除の管理者承認フロー(現状は自動解除のみ)

## 実機デプロイ時の注意

- `enableClaudeCodeProvisioning`はデフォルト`false`。有効化するとCognitoサインアップの度にIAMユーザー・アクセスキーが発行されるため、意図せぬ既存環境での有効化に注意
- CUR 2.0データはAWS側でS3配信されるまで最大24時間程度のラグがある。デプロイ直後は同期Lambdaが「まだデータなし」の状態(`_find_cur_table_name`が`RuntimeError`)になるのが正常
- Glue Crawlerは日次実行のため、CUR初回配信後も最大1日はテーブルが検出されない場合がある

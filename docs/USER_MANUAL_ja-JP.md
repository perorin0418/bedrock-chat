# ユーザーマニュアル

## 概要

このドキュメントは、権限レベルに応じて用意されている3つの利用方法をまとめたものです。

| 利用方法 | 必要な権限 | 概要 |
|---|---|---|
| [1. ボット経由のチャット](#1-ボット経由のチャット) | 特になし（サインインのみ） | アプリのチャットUIからボットと対話する、最も基本的な利用方法 |
| [2. 公開API（REST）経由でのやりとり](#2-公開apirest経由でのやりとり) | `PublishAllowed` グループ（公開する場合）／APIキー保有者（利用する場合） | 公開されたボットに対してHTTP経由で直接やりとりする方法 |
| [3. Bedrock APIキーを使ったClaude Codeへのアクセス](#3-bedrock-apiキーを使ったclaude-codeへのアクセス) | Claude Code連携機能が有効な環境の全ユーザー | Claude Code CLIから直接Amazon Bedrockを呼び出す方法 |

権限グループ（`Admin` / `CreatingBotAllowed` / `PublishAllowed`）の付与方法については[管理者向けドキュメント](./ADMINISTRATOR_ja-JP.md)を参照してください。

---

## 1. ボット経由のチャット

追加の権限なしに、サインインした全ユーザーが利用できます。

1. デプロイ時に払い出されるフロントエンドURLにアクセスします。URLはCloudFormationコンソールの `BedrockChatStack` > `Outputs` > `FrontendURL` から確認できます。
2. サインイン（またはセルフサインアップが有効な場合は新規登録）します。
3. 左側のメニューから、あらかじめ共有されているボットストア内のボットを選択するか、デフォルトのチャットを開きます。
4. メッセージを入力して送信すると、Amazon Bedrock上のモデルから応答が返ります。

独自のボット（RAGナレッジ付きボットなど）を作成するには `CreatingBotAllowed` グループへの参加が必要です。詳細は[README](./README_ja-JP.md)の「ボットのパーソナライゼーション / ボットストア」を参照してください。

---

## 2. 公開API（REST）経由でのやりとり

### 前提条件

- ボットを公開できるのは `PublishAllowed` グループのメンバーのみです。
- 公開されていないボットにはAPIでアクセスできません。

### ボットの公開とAPIキーの取得

1. `PublishAllowed` ユーザーとしてサインインし、共有設定済みのボットの詳細画面から `API PublishSettings` を選択します。
2. スロットリングなどのパラメータを設定してデプロイします。
3. デプロイ完了後の画面で、エンドポイントURLとAPIキーが表示されます（APIキーの追加・削除も可能）。

手順の詳細・スクリーンショットは[API公開ドキュメント](./PUBLISH_API_ja-JP.md)を参照してください。

### REST APIの呼び出し方

取得したエンドポイントURLとAPIキーを使い、リクエストヘッダーに `x-api-key` を設定して呼び出します。

```bash
export API_ENDPOINT="https://xxxxxxxxxx.execute-api.<region>.amazonaws.com/api"
export API_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

#### メッセージ送信（`POST /conversation`）

出力生成がAPI Gatewayのクォータ（30秒）を超える場合があるため、このAPIは非同期です。リクエストは即座に `conversation_id` と `message_id` を返し、実際の応答は裏側で生成されます。

```bash
curl -X POST "$API_ENDPOINT/conversation" \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "content": [
        { "contentType": "text", "body": "こんにちは" }
      ],
      "model": "claude-v4.5-sonnet"
    }
  }'
```

```json
{ "conversationId": "01J...", "messageId": "01J..." }
```

`model` に指定できる値は `backend/app/routes/schemas/conversation.py` の `type_model_name` を参照してください（例: `claude-v4.5-sonnet`、`claude-v4.5-haiku` など）。

#### 応答の取得（`GET /conversation/{conversation_id}/{message_id}`）

上記で返った `conversation_id` / `message_id` を使い、生成が完了するまでポーリングします。

```bash
curl -X GET "$API_ENDPOINT/conversation/$CONVERSATION_ID/$MESSAGE_ID" \
  -H "x-api-key: $API_KEY"
```

#### 会話履歴全体の取得（`GET /conversation/{conversation_id}`）

```bash
curl -X GET "$API_ENDPOINT/conversation/$CONVERSATION_ID" \
  -H "x-api-key: $API_KEY"
```

APIキーが特定ユーザーに紐づけて発行されている場合、そのユーザーのレート制限（5時間／7日間のローリングウィンドウ）が適用されます。

---

## 3. Bedrock APIキーを使ったClaude Codeへのアクセス

社員がClaude Code CLIから直接Amazon Bedrockを呼び出すための機能です。デプロイ時に `enableClaudeCodeProvisioning`（デフォルト有効）が設定されている場合、Cognitoサインアップ時にユーザーごとの専用IAMユーザーとアクセスキーが自動発行され、Secrets Manager（シークレット名 `claude-code/<user_id>`）に保管されます。アクセスキーは長期間有効な認証情報のため、自己サービスでの取得機能は提供されていません。**管理者がAWS CLI経由で取得し、社内の安全な方法で本人へ手渡してください。**

### 管理者向け：アクセスキー／シークレットキーの取得

```bash
# ユーザープールID（CloudFormation > BedrockChatStack > Outputs > AuthUserPoolIdxxxx で確認）
export USER_POOL_ID="<ユーザープールID>"
export USER_EMAIL="employee@example.com"

# メールアドレスからCognitoの sub（= Secrets Manager のシークレット名に使う user_id）を取得
export SUB=$(aws cognito-idp list-users \
  --user-pool-id "$USER_POOL_ID" \
  --filter "email = \"$USER_EMAIL\"" \
  --query "Users[0].Attributes[?Name=='sub'].Value | [0]" \
  --output text)
echo "user_id (sub): $SUB"

# Secrets ManagerからAPIキー（AccessKeyId）とシークレットキー（SecretAccessKey）を取得
aws secretsmanager get-secret-value \
  --secret-id "claude-code/$SUB" \
  --query SecretString --output text | jq .
```

出力される `AccessKeyId` と `SecretAccessKey` を、本人にのみ安全な方法（社内シークレット共有ツールなど）で共有してください。

### 利用者向け：Claude Codeのインストールと設定

#### インストール

```bash
npm install -g @anthropic-ai/claude-code
```

Node.js/npmを使わない場合は、公式のネイティブインストーラーも利用できます。

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

#### Bedrock経由で使うための環境変数設定

管理者から受け取った `AccessKeyId` / `SecretAccessKey` を使って、以下の環境変数を設定します。

```bash
export AWS_ACCESS_KEY_ID="<受け取ったAccessKeyId>"
export AWS_SECRET_ACCESS_KEY="<受け取ったSecretAccessKey>"
export AWS_REGION="us-east-1"                     # 管理者に確認した bedrockRegion（cdk.json）
export CLAUDE_CODE_USE_BEDROCK=1
export ANTHROPIC_MODEL="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
export ANTHROPIC_SMALL_FAST_MODEL="us.anthropic.claude-haiku-4-5-20251001-v1:0"
```

`ANTHROPIC_MODEL` のリージョンプレフィックス（`us.` / `apac.` / `eu.` / `global.` など）は、デプロイ先の `bedrockRegion` に合わせて変更してください。

#### 起動

```bash
claude
```

### 利用上の注意

- このアクセス経路はBedrock Chatのバックエンドを経由しないため、アプリ内チャットのようにリアルタイムではレート制限されません。Claude Codeの利用コストは1時間ごとのバッチ処理でBedrock Chat側の利用台帳に合算され、アプリ内チャットとの合計が5時間／7日間の上限を超えると、当該IAMユーザーへ自動的にBedrock呼び出しを拒否するポリシーが付与されます。
- 詳細な仕組み・管理者向けのデプロイ手順は[Claude Code Cost Sync](./CLAUDE_CODE_COST_SYNC.md)を参照してください。
- 退職時などのIAMユーザー・アクセスキーの削除は自動化されていません。管理者が手動で対応してください。

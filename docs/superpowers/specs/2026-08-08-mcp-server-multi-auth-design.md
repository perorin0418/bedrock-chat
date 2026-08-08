# MCPサーバー登録 複数認証方式対応 設計書

## 背景・目的

[固定MCPサーバー連携 設計書](2026-08-01-mcp-server-integration-design.md)で実装されたMCP連携機構は、acrocity-rag-system(自社KB用MCPサーバー)向けに、Cognito `client_credentials` grant一択で作られている。

Atlassian Rovo MCP Server(`https://mcp.atlassian.com/v1/mcp` 等)をはじめ、外部の一般的なMCPサーバーは以下のような別の認証方式を要求する:

- APIトークンをそのまま `Authorization: Bearer <token>` で送る方式(Rovoのサービスアカウントキー等)
- `email:token` を `Authorization: Basic <base64>` で送る方式(Rovoの個人APIトークン等)
- 認証ヘッダー自体が不要(URLのみで接続できるMCPサーバー)

これらに対応するため、bot単位のMCPサーバー設定に**認証方式(`auth_type`)の選択**を導入する。既存のCognito方式は「複数ある認証方式の1つ」として位置づけ直し、既存bot・既存データへの影響なく新方式を追加する。

## スコープ

- 対応する認証方式: `cognito_client_credentials`(既存、デフォルト) / `none` / `bearer_token` / `basic_auth`
- Rovo固有の情報(ドメイン名、固定エンドポイント等)はコードに埋め込まない。`endpoint_url`は既存通り自由入力(https必須)。あくまで汎用の認証方式追加であり、Rovoはその利用例の1つ
- OAuth 2.1のような対話的同意フロー(ブラウザリダイレクト・トークン保存・リフレッシュ)は**スコープ外**。ユーザー判断により今回は見送り、必要になれば別設計とする
- 既存bot・既存DynamoDB項目・既存Secrets Manager格納形式への後方互換を必須とする(マイグレーションスクリプトを書かない)

## データモデル

### `backend/app/routes/schemas/bot.py` — `McpConfig`

現状(L140-176)、`label`/`endpoint_url`/`client_id`/`client_secret`が全て必須。以下のように変更する:

```python
class McpAuthType(str, Enum):
    COGNITO_CLIENT_CREDENTIALS = "cognito_client_credentials"
    NONE = "none"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"

class McpConfig(BaseSchema):
    label: str
    endpoint_url: str
    auth_type: McpAuthType = McpAuthType.COGNITO_CLIENT_CREDENTIALS

    # cognito_client_credentials用
    client_id: str | None = None
    client_secret: str | None = None
    # bearer_token用
    bearer_token: str | None = None
    # basic_auth用
    username: str | None = None
    basic_auth_token: str | None = None

    # 既存の label / endpoint_url validator は変更なし

    @model_validator(mode="after")
    def validate_auth_fields(self):
        required = {
            McpAuthType.COGNITO_CLIENT_CREDENTIALS: ["client_id", "client_secret"],
            McpAuthType.NONE: [],
            McpAuthType.BEARER_TOKEN: ["bearer_token"],
            McpAuthType.BASIC_AUTH: ["username", "basic_auth_token"],
        }
        all_auth_fields = {"client_id", "client_secret", "bearer_token", "username", "basic_auth_token"}
        needed = set(required[self.auth_type])
        for field in needed:
            if not getattr(self, field):
                raise ValueError(f"'{field}' is required when auth_type is '{self.auth_type}'")
        for field in all_auth_fields - needed:
            if getattr(self, field):
                raise ValueError(f"'{field}' must not be set when auth_type is '{self.auth_type}'")
        return self
```

- `auth_type`省略時は`COGNITO_CLIENT_CREDENTIALS`→既存クライアント(フロントエンド未対応時)からのリクエストも無改修で通る
- 「選択中の`auth_type`に不要なフィールドが埋まっていたらエラー」により、設定の取り違え(例: `none`を選びつつ`bearer_token`も送る)をAPI層で弾く

### `backend/app/repositories/models/custom_bot.py` — `McpConfigModel` / `McpToolModel`

現状(L290-305)、`label`/`endpoint_url`/`client_id`/`client_secret: SecureString`。`McpToolModel`(L308-381)は「`{label: client_secret}`のJSONを1つのSecrets Manager entryにまとめて格納・復元する」`_get_secret`/`_set_secret`ヘルパー(L327-360付近)を持つ。

変更方針:

```python
class McpConfigModel(BaseModel):
    label: str
    endpoint_url: str
    auth_type: McpAuthType = McpAuthType.COGNITO_CLIENT_CREDENTIALS
    client_id: str | None = None
    client_secret: SecureString | None = Field(None, repr=False)
    bearer_token: SecureString | None = Field(None, repr=False)
    username: str | None = None
    basic_auth_token: SecureString | None = Field(None, repr=False)
```

- `auth_type`のDynamoDB上のデフォルト欠損時挙動はpydanticのデフォルト値で吸収(既存項目に`auth_type`キーが無くても`COGNITO_CLIENT_CREDENTIALS`として読める)
- Secrets Manager格納は**「認証方式ごとに秘密情報を持つフィールドは1つだけ」という既存の設計をそのまま流用**する。`_get_secret`/`_set_secret`を「`client_secret`固定」から「`auth_type`に応じた1フィールド(`client_secret` / `bearer_token` / `basic_auth_token` / なし)」を読み書きするよう汎用化する。JSON blobのキー(`{label: <secret文字列>}`)自体の形は変えない → **CDK側の`secret:mcp/*/*`権限、Secrets Manager格納形式は無改修**
- `username`は`client_id`と同様に非機密情報としてDynamoDB項目に平置き(Secrets Managerに入れない)

## 実行ロジック(`backend/app/strands_integration/tools/mcp_tools.py`)

現状`mcp_tools_scope`(L136-205)は、サーバーごとに無条件で`get_mcp_bearer_token`(Cognito固定)を呼び、`Authorization: Bearer <token>`ヘッダーを付けて`streamablehttp_client`に接続している(L157-169)。

`auth_type`に応じてヘッダー構築だけを分岐する関数を追加し、既存のループ構造(L154-203: per-server try/except、ExitStackでのclose管理、ツール名prefix付与)は変更しない:

```python
def _build_auth_headers(server: McpConfigModel, secret_arn: str | None) -> dict[str, str]:
    if server.auth_type == McpAuthType.NONE:
        return {}
    if server.auth_type == McpAuthType.BEARER_TOKEN:
        return {"Authorization": f"Bearer {server.bearer_token}"}
    if server.auth_type == McpAuthType.BASIC_AUTH:
        credentials = base64.b64encode(
            f"{server.username}:{server.basic_auth_token}".encode()
        ).decode()
        return {"Authorization": f"Basic {credentials}"}
    # COGNITO_CLIENT_CREDENTIALS(既存動作)
    token = get_mcp_bearer_token(
        server.client_id, server.client_secret, COGNITO_MCP_AUTH_DOMAIN,
        f"{secret_arn}:{server.label}",
    )
    return {"Authorization": f"Bearer {token}"}
```

`mcp_tools_scope`内のL157-169は`headers = _build_auth_headers(server, mcp_tool.secret_arn)`を`streamablehttp_client(server.endpoint_url, headers=headers, timeout=...)`に渡す形に置き換える。

- `bearer_token`/`basic_auth`はトークン取得の往復も有効期限管理も不要(既に最終トークンを保持しているだけ)なので、キャッシュ機構(`_token_cache`)は`cognito_client_credentials`専用のまま変更不要
- エラーハンドリングは既存のper-server try/except(L172-177, L183-188)がそのままカバーする。認証ヘッダーの組み立て自体は例外を投げない(必須フィールドはAPI層のバリデーションで既に保証済み)ため、実際にエラーになるのは接続失敗時(401/403含む)で、これは既存の「その1サーバーだけスキップしてログ出力、他は続行」という縮退運転にそのまま乗る

## フロントエンド

`frontend/src/features/agent/components/McpConfig.tsx`(現状41行、label/endpointUrl/clientId/clientSecretの固定4項目フォーム)に、認証タイプのセレクトを追加し、選択に応じて入力項目を出し分ける:

- `cognito_client_credentials`: 既存通り clientId / clientSecret
- `none`: 追加入力項目なし(label / endpointUrlのみ)
- `bearer_token`: token 1項目
- `basic_auth`: username / token 2項目

`features/agent/types/index.d.ts`の型定義に`authType`(既存フィールドは全て`?`任意化)を追加。保存前バリデーション(`McpServersConfig.tsx`側、または各行コンポーネント側)は、backendの`model_validator`と同じ「選択中のauth_typeに必要な項目が埋まっているか」をクライアント側でも先出しチェックし、送信前にエラー表示する。

## エラーハンドリング

- 設定値の不整合(必須項目欠落・対象外項目への値設定)はAPI層(`McpConfig`のpydantic validator)で422として弾く。既存の[MCP tool validation errorのサーフェス化修正](2026-08-01の後続fix: `fix/mcp-bot-creation-validation`)がこの422をUIに表示する経路として既に存在するため、追加改修不要
- 実行時(接続失敗、認証エラー等)は既存の「サーバー単位で縮退運転・ログ出力・他サーバー継続」を全auth_typeで共通適用。新規のUI通知は行わない(既存方針を維持、スコープ外)

## テスト方針

- `McpConfig`の4 auth_typeそれぞれに対する必須/禁止フィールドのバリデーション単体テストを追加(pydanticレベル、AWS不要)
- `_build_auth_headers`のヘッダー構築ロジックを、auth_typeごとに単体テストで検証(Cognitoトークン取得部分は既存同様HTTPモック)
- Secrets Managerへの格納/復元(`_get_secret`/`_set_secret`汎用化後)の往復テストを、4 auth_type分追加
- 実際のRovo MCP Serverへの疎通確認は、既存方針([2026-08-01設計書](2026-08-01-mcp-server-integration-design.md)の「テスト方針」)を踏襲し自動テスト化せず、手動でAPIトークンを使った疎通確認を行う

## スコープ外(明示)

- OAuth 2.1対話的同意フロー(ブラウザリダイレクト、per-userトークン保存、リフレッシュ)
- 任意HTTPヘッダーを複数指定できる汎用認証方式(今回は`bearer_token`/`basic_auth`の2具体型のみ)
- MCPサーバー登録・一覧管理のための管理者向け専用画面(bot単位で手入力する既存の形を維持)
- 既存データのマイグレーションスクリプト(不要という設計そのものがスコープ)

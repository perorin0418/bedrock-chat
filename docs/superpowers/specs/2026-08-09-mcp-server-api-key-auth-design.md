# MCPサーバー登録 APIキー(x-api-key)認証対応 設計書

## 背景・目的

[MCPサーバー登録 複数認証方式対応 設計書](2026-08-08-mcp-server-multi-auth-design.md)で、bot単位のMCPサーバー設定は`auth_type`(`cognito_client_credentials` / `none` / `bearer_token` / `basic_auth`)を選択できるようになった。

一方、Amazon API Gatewayのネイティブ「APIキー必須」機能(usage plan紐付け)は、APIキーを**ヘッダー名`x-api-key`固定**で送ることを要求する(AWS仕様上、ヘッダー名は変更不可)。既存の`bearer_token`(`Authorization: Bearer <token>`)や`basic_auth`(`Authorization: Basic <base64>`)はいずれも`Authorization`ヘッダーを使うため、API Gatewayのこの機能には接続できない。

これに対応するため、既存4方式と同列の5番目の`auth_type`として`api_key`を追加する。

## スコープ

- 対応する認証方式に`api_key`を追加(既存4方式は無改修)
- ヘッダー名は`x-api-key`に**固定**する(可変ヘッダー名の汎用化はスコープ外。API Gatewayネイティブ機能への対応が目的であり、そこでは固定値のため)
- 既存bot・既存DynamoDB項目・既存Secrets Manager格納形式への後方互換を必須とする(マイグレーションスクリプトを書かない)
- API Gateway usage plan / APIキー自体のCDK設定変更はスコープ外(MCPクライアント側の接続対応のみ)

## データモデル

### `backend/app/routes/schemas/bot.py` — `McpConfig`

既存の`McpAuthType`, `MCP_AUTH_REQUIRED_FIELDS`, `MCP_AUTH_ALL_FIELDS`にそれぞれ追加する:

```python
class McpAuthType(str, Enum):
    COGNITO_CLIENT_CREDENTIALS = "cognito_client_credentials"
    NONE = "none"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"
    API_KEY = "api_key"


MCP_AUTH_REQUIRED_FIELDS: dict[McpAuthType, list[str]] = {
    McpAuthType.COGNITO_CLIENT_CREDENTIALS: ["client_id", "client_secret"],
    McpAuthType.NONE: [],
    McpAuthType.BEARER_TOKEN: ["bearer_token"],
    McpAuthType.BASIC_AUTH: ["username", "basic_auth_token"],
    McpAuthType.API_KEY: ["api_key"],
}
MCP_AUTH_ALL_FIELDS = {
    "client_id",
    "client_secret",
    "bearer_token",
    "username",
    "basic_auth_token",
    "api_key",
}


class McpConfig(BaseSchema):
    label: str
    endpoint_url: str
    auth_type: McpAuthType = McpAuthType.COGNITO_CLIENT_CREDENTIALS
    client_id: str | None = None
    client_secret: str | None = None
    bearer_token: str | None = None
    username: str | None = None
    basic_auth_token: str | None = None
    api_key: str | None = None
```

既存の`model_validator`(選択中`auth_type`に必要/禁止フィールドが埋まっているかのチェック)はこのマップを参照するだけなので無改修で`api_key`に対応する。

### `backend/app/repositories/models/custom_bot.py` — `McpConfigModel` / `_mcp_secret_field_name`

```python
def _mcp_secret_field_name(auth_type: McpAuthType) -> str | None:
    return {
        McpAuthType.COGNITO_CLIENT_CREDENTIALS: "client_secret",
        McpAuthType.BEARER_TOKEN: "bearer_token",
        McpAuthType.BASIC_AUTH: "basic_auth_token",
        McpAuthType.API_KEY: "api_key",
        McpAuthType.NONE: None,
    }[auth_type]


class McpConfigModel(BaseModel):
    label: str
    endpoint_url: str
    auth_type: McpAuthType = McpAuthType.COGNITO_CLIENT_CREDENTIALS
    client_id: str | None = None
    client_secret: SecureString | None = Field(None, repr=False)
    bearer_token: SecureString | None = Field(None, repr=False)
    username: str | None = None
    basic_auth_token: SecureString | None = Field(None, repr=False)
    api_key: SecureString | None = Field(None, repr=False)
```

`McpToolModel`のSecrets Manager往復ロジック(`_get_secret`/`_set_secret`、`load_mcp_secrets`, `from_tool_input`)は`_mcp_secret_field_name`経由で汎用化済みのため無改修で`api_key`に対応する。JSON blobのキー形式(`{label: <secret文字列>}`)、CDK側の`secret:mcp/*/*`権限も無改修。

## 実行ロジック(`backend/app/strands_integration/tools/mcp_tools.py`)

`_build_auth_headers`に分岐を1つ追加する:

```python
def _build_auth_headers(
    server: McpConfigModel, secret_arn: str | None
) -> dict[str, str]:
    if server.auth_type == McpAuthType.NONE:
        return {}

    if server.auth_type == McpAuthType.BEARER_TOKEN:
        return {"Authorization": f"Bearer {server.bearer_token}"}

    if server.auth_type == McpAuthType.BASIC_AUTH:
        credentials = base64.b64encode(
            f"{server.username}:{server.basic_auth_token}".encode()
        ).decode()
        return {"Authorization": f"Basic {credentials}"}

    if server.auth_type == McpAuthType.API_KEY:
        return {"x-api-key": cast(str, server.api_key)}

    # McpAuthType.COGNITO_CLIENT_CREDENTIALS(既存動作)
    ...
```

- 呼び出し元`mcp_tools_scope`のループ構造(per-server try/except、ExitStackでのclose管理、ツール名prefix付与)は無改修
- キャッシュ機構(`_token_cache`)は`cognito_client_credentials`専用のまま変更不要(`api_key`はトークン取得の往復がなく、保持している値をそのままヘッダーに使うだけ)
- エラーハンドリングは既存のper-server try/exceptがそのままカバーする(APIキー不正時は接続失敗として、その1サーバーだけスキップしログ出力、他は続行)

## フロントエンド

`frontend/src/features/agent/types/index.d.ts`:

```typescript
export type McpAuthType =
  | 'cognito_client_credentials'
  | 'none'
  | 'bearer_token'
  | 'basic_auth'
  | 'api_key';

export type McpConfig = {
  label: string;
  endpointUrl: string;
  authType: McpAuthType;
  clientId?: string;
  clientSecret?: string;
  bearerToken?: string;
  username?: string;
  basicAuthToken?: string;
  apiKey?: string;
};
```

`frontend/src/features/agent/components/McpConfig.tsx`:

- `AUTH_TYPES`配列に`'api_key'`を追加
- 認証タイプSelectの`onChange`で他フィールドをクリアする処理に`apiKey: undefined`を追加
- `config.authType === 'api_key'`のとき、`bearer_token`と同構成で`InputText type="password"`を1つ表示(`agent.tools.mcpConfig.apiKey.label`/`.placeholder`)

`McpServersConfig.tsx`のデフォルト値・保存前バリデーション(backendの`model_validator`と同じ「選択中authTypeに必要な項目が埋まっているか」チェック)は既存のフィールド一覧に`apiKey`/`api_key`を追加するだけで、ロジック自体は既存4方式と同じ形を流用する。

## i18n

既存の`bearer_token`/`basic_auth`と同様、`ja`/`en`のみ追加する(他14言語は現状この機能で未対応のまま、既存方針を踏襲):

- `authType.options.api_key`: 「APIキー(x-api-key)」/ `"API Key (x-api-key)"`
- `apiKey.label`/`.placeholder`: 「APIキー」/ `"API Key"` (placeholder例: API Gatewayの使用量プランで発行されたAPIキーの値)

## ドキュメント

`docs/AGENT.md`の認証方式テーブル(L181-186)に5行目を追加する:

| 認証方式 | 用途 | 必要な入力項目 |
|---|---|---|
| APIキー(x-api-key) | Amazon API Gatewayのネイティブ「APIキー必須」機能等、`x-api-key`ヘッダーでAPIキーを要求するMCPサーバーに接続する | APIキー |

## テスト方針

- `McpConfig`の`api_key`に対する必須/禁止フィールドのバリデーション単体テストを追加(既存4方式の単体テストと同形式)
- `_build_auth_headers`の`API_KEY`分岐を単体テストで検証(`{"x-api-key": <値>}`が返ることを確認)
- Secrets Managerへの格納/復元往復テストを`api_key`分追加

## スコープ外(明示)

- ヘッダー名を可変にする汎用APIキー方式(今回は`x-api-key`固定の1方式のみ)
- API Gateway usage plan / APIキー自体の発行・CDK設定変更
- 既存データのマイグレーションスクリプト

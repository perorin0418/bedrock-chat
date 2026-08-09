# MCPサーバー APIキー(x-api-key)認証対応 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** bot単位のMCPサーバー設定に5番目の認証方式`api_key`を追加し、`x-api-key`ヘッダーでAPIキーを要求するMCPサーバー(Amazon API Gatewayのネイティブ「APIキー必須」機能を含む)に接続できるようにする。

**Architecture:** 既存の`auth_type`(`cognito_client_credentials`/`none`/`bearer_token`/`basic_auth`)と全く同じパターンで`api_key`を追加する。backend側は「enum追加 → 必須/禁止フィールドのマップに1行追加 → Secrets Manager格納フィールドのマップに1行追加 → ヘッダー構築に1分岐追加」の4点のみで、既存の汎用ロジック(pydanticバリデーション、Secrets Manager往復、per-serverエラーハンドリング)は無改修。frontend側も同様に、型・UI・クライアント側バリデーションのそれぞれに既存4方式と同じ形で1エントリ追加する。

**Tech Stack:** Python 3.13 / FastAPI / pydantic v2(backend)、React + TypeScript(frontend)。

## Global Constraints

- ヘッダー名は`x-api-key`に**固定**する(可変ヘッダー名は対象外)
- 既存bot・既存DynamoDB項目・既存Secrets Manager格納形式への後方互換を維持する(マイグレーションスクリプトを書かない)
- i18nは既存の`bearer_token`/`basic_auth`と同様、`ja`/`en`のみ追加する(他14言語は対象外)
- API Gateway usage plan / APIキー自体の発行・CDK設定変更は対象外
- 設計の詳細根拠は [`docs/superpowers/specs/2026-08-09-mcp-server-api-key-auth-design.md`](../specs/2026-08-09-mcp-server-api-key-auth-design.md) を参照

## 既知の環境注意事項(このリポジトリ固有、対応不要)

- `backend/mypy.ini`実行時、`s3_exporter/index.py`と`claude_code_cost_sync/index.py`のモジュール名重複による既存の(本タスクと無関係な)mypyエラーが1件出る。今回の変更前から存在する既知の問題であり、本プランの範囲外。

---

## Task 1: Backend — `McpAuthType`に`api_key`を追加(スキーマ・バリデーション)

**Files:**
- Modify: `backend/app/routes/schemas/bot.py:142-172`
- Test: `backend/tests/test_routes/test_schemas/test_bot_mcp_config.py`

**Interfaces:**
- Produces: `McpAuthType.API_KEY`(値`"api_key"`)、`McpConfig.api_key: str | None`。Task 2以降がこの`McpAuthType.API_KEY`と`api_key`フィールド名をそのまま使う。

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_routes/test_schemas/test_bot_mcp_config.py`の`TestMcpConfigAuthType`クラス末尾(83行目の`test_basic_auth_valid`の後、86行目の`if __name__ ==`の前)に追記:

```python
    def test_api_key_requires_api_key_field(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.API_KEY,
            )

    def test_api_key_valid(self):
        config = McpConfig(
            label="foo",
            endpoint_url="https://example.com/mcp",
            auth_type=McpAuthType.API_KEY,
            api_key="key-1",
        )
        self.assertEqual(config.api_key, "key-1")

    def test_api_key_rejects_other_auth_fields(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.API_KEY,
                api_key="key-1",
                bearer_token="should-not-be-here",
            )
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd backend && poetry run python tests/test_routes/test_schemas/test_bot_mcp_config.py -v`
Expected: `AttributeError: API_KEY` (または`McpAuthType`に`API_KEY`が存在しないエラー)でFAIL

- [ ] **Step 3: 実装する**

`backend/app/routes/schemas/bot.py`の142-161行目を以下に置き換える:

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
```

`McpConfig`クラス(164-172行目)に`api_key`フィールドを追加する:

```python
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

既存の`validate_auth_fields`(`model_validator(mode="after")`)は`MCP_AUTH_REQUIRED_FIELDS`/`MCP_AUTH_ALL_FIELDS`を参照するだけなので無改修。

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `cd backend && poetry run python tests/test_routes/test_schemas/test_bot_mcp_config.py -v`
Expected: `Ran 11 tests in ...s` / `OK`(既存8件+新規3件)

- [ ] **Step 5: コミット**

```bash
git add backend/app/routes/schemas/bot.py backend/tests/test_routes/test_schemas/test_bot_mcp_config.py
git commit -m "feat: add api_key auth type to MCP server config schema"
```

---

## Task 2: Backend — `McpConfigModel`に`api_key`フィールドとSecrets Manager格納対応を追加

**Files:**
- Modify: `backend/app/repositories/models/custom_bot.py:291-323`
- Test: `backend/tests/test_repositories/test_models/test_mcp_tool.py`

**Interfaces:**
- Consumes: `McpAuthType.API_KEY`(Task 1で追加)
- Produces: `McpConfigModel.api_key: SecureString | None`。Task 3が`server.api_key`を読む。

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_repositories/test_models/test_mcp_tool.py`の`test_from_agent_input_stores_basic_auth_secret`(158-190行目)の後に追記:

```python
    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_stores_api_key_secret(self, mock_store):
        mock_store.return_value = (
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1"
        )

        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpServers=[
                        McpConfig(
                            label="gateway",
                            endpoint_url="https://abc123.execute-api.ap-northeast-1.amazonaws.com/prod/mcp",
                            auth_type="api_key",
                            api_key="apigw-key-1",
                        ),
                    ],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        tool = agent_model.tools[0]
        self.assertEqual(tool.mcpServers[0].api_key, "apigw-key-1")
        mock_store.assert_called_once_with(
            "user1", "bot1", "mcp", json.dumps({"gateway": "apigw-key-1"})
        )
```

`test_to_agent_round_trips_bearer_token`(254-279行目)の後に追記:

```python
    @patch("app.repositories.models.custom_bot.get_api_key_from_secret_manager")
    def test_to_agent_round_trips_api_key(self, mock_get_secret):
        mock_get_secret.return_value = json.dumps({"gateway": "apigw-key-1"})

        agent_model = AgentModel(
            tools=[
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
                    mcpServers=[
                        McpConfigModel(
                            label="gateway",
                            endpoint_url="https://abc123.execute-api.ap-northeast-1.amazonaws.com/prod/mcp",
                            auth_type="api_key",
                            api_key="",
                        ),
                    ],
                )
            ]
        )

        agent = agent_model.to_agent()

        self.assertEqual(agent.tools[0].mcpServers[0].api_key, "apigw-key-1")
```

`test_model_dump_blanks_secret_fields_for_every_auth_type`(315-332行目)の末尾(`self.assertEqual(basic_model.model_dump()["username"], "me@example.com")`の直後)に追記:

```python

        api_key_model = McpConfigModel(
            label="gateway",
            endpoint_url="https://abc123.execute-api.ap-northeast-1.amazonaws.com/prod/mcp",
            auth_type="api_key",
            api_key="do-not-leak-this-key",
        )
        self.assertEqual(api_key_model.model_dump()["api_key"], "")
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd backend && poetry run python tests/test_repositories/test_models/test_mcp_tool.py -v`
Expected: `pydantic_core._pydantic_core.ValidationError` または `AttributeError`(`McpConfigModel`に`api_key`フィールドが無い/`auth_type="api_key"`が無効)でFAIL

- [ ] **Step 3: 実装する**

`backend/app/repositories/models/custom_bot.py`の`_mcp_secret_field_name`(291-297行目)と`McpConfigModel`(300-323行目)を以下に置き換える:

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

    @classmethod
    def from_mcp_config(cls, config: McpConfig) -> Self:
        """Create a configuration model from the input (secret storage is handled once,
        for all servers together, at the McpToolModel level)."""
        return cls(
            label=config.label,
            endpoint_url=config.endpoint_url,
            auth_type=config.auth_type,
            client_id=config.client_id,
            client_secret=config.client_secret,
            bearer_token=config.bearer_token,
            username=config.username,
            basic_auth_token=config.basic_auth_token,
            api_key=config.api_key,
        )
```

`McpToolModel.load_mcp_secrets`/`from_tool_input`は`_mcp_secret_field_name`経由で汎用化済みのため無改修。

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `cd backend && poetry run python tests/test_repositories/test_models/test_mcp_tool.py -v`
Expected: `Ran 14 tests in ...s` / `OK`(既存11件+新規3件)

- [ ] **Step 5: コミット**

```bash
git add backend/app/repositories/models/custom_bot.py backend/tests/test_repositories/test_models/test_mcp_tool.py
git commit -m "feat: store and round-trip api_key secret for MCP server config"
```

---

## Task 3: Backend — `_build_auth_headers`に`x-api-key`ヘッダー分岐を追加

**Files:**
- Modify: `backend/app/strands_integration/tools/mcp_tools.py:89-113`
- Test: `backend/tests/test_strands_integration/test_mcp_tools.py`

**Interfaces:**
- Consumes: `McpAuthType.API_KEY`(Task 1)、`McpConfigModel.api_key`(Task 2)
- Produces: `_build_auth_headers`が`auth_type == API_KEY`のとき`{"x-api-key": <値>}`を返す。呼び出し元`mcp_tools_scope`(既存、無改修)がこの戻り値をそのまま`streamablehttp_client(..., headers=headers, ...)`に渡す。

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_strands_integration/test_mcp_tools.py`の`test_basic_auth_returns_base64_basic_header`(114-128行目)の後に追記:

```python
    def test_api_key_returns_x_api_key_header(self):
        server = McpConfigModel(
            label="gateway",
            endpoint_url="https://abc123.execute-api.ap-northeast-1.amazonaws.com/prod/mcp",
            auth_type=McpAuthType.API_KEY,
            api_key="apigw-key-1",
        )

        headers = _build_auth_headers(server, secret_arn=None)

        self.assertEqual(headers, {"x-api-key": "apigw-key-1"})
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd backend && poetry run python tests/test_strands_integration/test_mcp_tools.py -v`
Expected: `pydantic_core._pydantic_core.ValidationError`(`McpConfigModel`が`auth_type="api_key"`/`api_key`フィールドをまだ受け付けない)でFAIL

(Task 1・Task 2が先に完了していれば、代わりに`_build_auth_headers`がCOGNITO分岐にフォールスルーして`AssertionError: {'Authorization': ...} != {'x-api-key': ...}`でFAILする)

- [ ] **Step 3: 実装する**

`backend/app/strands_integration/tools/mcp_tools.py`の`_build_auth_headers`(89-113行目)の`BASIC_AUTH`分岐の直後、COGINTOフォールバックの手前に追加:

```python
    if server.auth_type == McpAuthType.BASIC_AUTH:
        credentials = base64.b64encode(
            f"{server.username}:{server.basic_auth_token}".encode()
        ).decode()
        return {"Authorization": f"Basic {credentials}"}

    if server.auth_type == McpAuthType.API_KEY:
        return {"x-api-key": cast(str, server.api_key)}

    # McpAuthType.COGNITO_CLIENT_CREDENTIALS (existing behavior)
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `cd backend && poetry run python tests/test_strands_integration/test_mcp_tools.py -v`
Expected: `Ran 22 tests in ...s` / `OK`(既存21件+新規1件)

- [ ] **Step 5: コミット**

```bash
git add backend/app/strands_integration/tools/mcp_tools.py backend/tests/test_strands_integration/test_mcp_tools.py
git commit -m "feat: send x-api-key header for MCP servers using api_key auth"
```

---

## Task 4: Frontend — 型定義に`api_key`を追加

**Files:**
- Modify: `frontend/src/features/agent/types/index.d.ts:23-38`

**Interfaces:**
- Produces: `McpAuthType`に`'api_key'`、`McpConfig`に`apiKey?: string`。Task 5〜7がこれを使う。

- [ ] **Step 1: 実装する**

`frontend/src/features/agent/types/index.d.ts`の23-38行目を以下に置き換える:

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

- [ ] **Step 2: 型チェックを実行する**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラーなく終了(この時点では`apiKey`/`'api_key'`はまだどこからも参照されていないため、既存コードに影響なし)

- [ ] **Step 3: コミット**

```bash
git add frontend/src/features/agent/types/index.d.ts
git commit -m "feat: add api_key to MCP auth type definitions"
```

---

## Task 5: Frontend — i18n(ja/en)に`apiKey`関連の文言を追加

**Files:**
- Modify: `frontend/src/i18n/ja/index.ts:293-331`
- Modify: `frontend/src/i18n/en/index.ts:292-330`

**Interfaces:**
- Produces: 翻訳キー`agent.tools.mcpConfig.authType.options.api_key`、`agent.tools.mcpConfig.apiKey.label`、`agent.tools.mcpConfig.apiKey.placeholder`。Task 6が`t()`でこれらを参照する。

- [ ] **Step 1: 実装する(ja)**

`frontend/src/i18n/ja/index.ts`の296-301行目(`authType.options`)を以下に置き換える:

```typescript
            options: {
              cognito_client_credentials: 'ボット間ナレッジ共有(Cognito)',
              none: '認証なし',
              bearer_token: 'Bearerトークン',
              basic_auth: 'Basic認証(ユーザー名+トークン)',
              api_key: 'APIキー(x-api-key)',
            },
```

同ファイルの327-330行目(`basicAuthToken`の後、`mcpConfig`オブジェクトの閉じ`},`の前)に追加:

```typescript
          basicAuthToken: {
            label: 'トークン',
            placeholder: 'APIトークンを入力',
          },
          apiKey: {
            label: 'APIキー',
            placeholder: 'API Gatewayの使用量プラン等で発行されたAPIキーを入力',
          },
```

- [ ] **Step 2: 実装する(en)**

`frontend/src/i18n/en/index.ts`の295-300行目(`authType.options`)を以下に置き換える:

```typescript
            options: {
              cognito_client_credentials: 'Bot-to-bot knowledge sharing (Cognito)',
              none: 'No authentication',
              bearer_token: 'Bearer token',
              basic_auth: 'Basic auth (username + token)',
              api_key: 'API Key (x-api-key)',
            },
```

同ファイルの326-329行目(`basicAuthToken`の後、`mcpConfig`オブジェクトの閉じ`},`の前)に追加:

```typescript
          basicAuthToken: {
            label: 'Token',
            placeholder: 'Enter the API token',
          },
          apiKey: {
            label: 'API Key',
            placeholder: 'Enter the API key (e.g. issued by an API Gateway usage plan)',
          },
```

- [ ] **Step 3: 型チェックとlintを実行する**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: エラーなく終了

- [ ] **Step 4: コミット**

```bash
git add frontend/src/i18n/ja/index.ts frontend/src/i18n/en/index.ts
git commit -m "feat: add ja/en i18n strings for MCP api_key auth type"
```

---

## Task 6: Frontend — `McpConfig.tsx`に`api_key`用の入力フィールドを追加

**Files:**
- Modify: `frontend/src/features/agent/components/McpConfig.tsx`

**Interfaces:**
- Consumes: `McpAuthType`/`McpConfig`(Task 4)、i18nキー`agent.tools.mcpConfig.authType.options.api_key` / `agent.tools.mcpConfig.apiKey.label` / `.placeholder`(Task 5)

- [ ] **Step 1: 実装する**

`frontend/src/features/agent/components/McpConfig.tsx`の`AUTH_TYPES`配列(11-16行目)を以下に置き換える:

```typescript
const AUTH_TYPES: McpAuthType[] = [
  'cognito_client_credentials',
  'none',
  'bearer_token',
  'basic_auth',
  'api_key',
];
```

認証タイプSelectの`onChange`(45-55行目)に`apiKey: undefined`を追加する:

```typescript
        onChange={(value) =>
          onChange({
            ...config,
            authType: value as McpAuthType,
            clientId: undefined,
            clientSecret: undefined,
            bearerToken: undefined,
            username: undefined,
            basicAuthToken: undefined,
            apiKey: undefined,
          })
        }
```

`basic_auth`の入力ブロック(83-99行目)の直後、`</div>`(100行目)の前に追加:

```typescript
      {config.authType === 'api_key' && (
        <InputText
          type="password"
          label={t('agent.tools.mcpConfig.apiKey.label')}
          placeholder={t('agent.tools.mcpConfig.apiKey.placeholder')}
          value={config.apiKey ?? ''}
          onChange={(value) => onChange({ ...config, apiKey: value })}
        />
      )}
```

- [ ] **Step 2: 型チェック・lint・ビルドを実行する**

Run: `cd frontend && npx tsc --noEmit && npm run lint && npm run build`
Expected: エラーなく終了

- [ ] **Step 3: コミット**

```bash
git add frontend/src/features/agent/components/McpConfig.tsx
git commit -m "feat: add api_key input field to MCP server config form"
```

---

## Task 7: Frontend — `BotKbEditPage.tsx`の保存前バリデーションに`api_key`を追加

**Files:**
- Modify: `frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx:615-623`

**Interfaces:**
- Consumes: `McpAuthType`/`McpConfigType`(Task 4)

- [ ] **Step 1: 実装する**

`frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx`の`requiredFieldsByAuthType`(615-623行目)を以下に置き換える:

```typescript
        const requiredFieldsByAuthType: Record<
          McpAuthType,
          (keyof McpConfigType)[]
        > = {
          cognito_client_credentials: ['clientId', 'clientSecret'],
          none: [],
          bearer_token: ['bearerToken'],
          basic_auth: ['username', 'basicAuthToken'],
          api_key: ['apiKey'],
        };
```

- [ ] **Step 2: 型チェック・lint・ビルドを実行する**

Run: `cd frontend && npx tsc --noEmit && npm run lint && npm run build`
Expected: エラーなく終了(`Record<McpAuthType, ...>`は全キー必須のため、`api_key`を追加し忘れると`tsc`がここでコンパイルエラーを出す)

- [ ] **Step 3: コミット**

```bash
git add frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx
git commit -m "feat: require apiKey field when MCP server auth_type is api_key"
```

---

## Task 8: Docs — `AGENT.md`の認証方式テーブルに`api_key`を追加

**Files:**
- Modify: `docs/AGENT.md:179-186`

- [ ] **Step 1: 実装する**

`docs/AGENT.md`の179-186行目を以下に置き換える:

```markdown
bot作成/編集画面の「MCPサーバー」ツールで、外部のMCPサーバーを登録できる。サーバーごとに以下5つの認証方式から選択する:

| 認証方式 | 用途 | 必要な入力項目 |
|---|---|---|
| ボット間ナレッジ共有(Cognito) | 自社の他botが公開するナレッジベースMCPサーバーに接続する | Client ID, Client Secret |
| 認証なし | 認証を要求しない公開MCPサーバーに接続する | (URLのみ) |
| Bearerトークン | 固定トークンを`Authorization: Bearer <token>`で送るMCPサーバーに接続する | トークン |
| Basic認証(ユーザー名+トークン) | `Authorization: Basic <base64>`を要求するMCPサーバーに接続する | ユーザー名, トークン |
| APIキー(x-api-key) | Amazon API Gatewayのネイティブ「APIキー必須」機能等、`x-api-key`ヘッダーでAPIキーを要求するMCPサーバーに接続する | APIキー |
```

- [ ] **Step 2: コミット**

```bash
git add docs/AGENT.md
git commit -m "docs: document the api_key MCP auth type"
```

---

## 完了後の手動確認(自動テスト対象外)

- ローカルで`npm run dev`を起動し、bot編集画面のMCPサーバー設定で「APIキー(x-api-key)」を選択→APIキー入力欄が表示されること、他方式に切り替えると値がクリアされることを目視確認する
- 実際にAPI Gatewayの「APIキー必須」usage planを持つエンドポイントに対して、登録したbotで疎通確認する(自動テスト化はスコープ外、既存のRovo疎通確認方針を踏襲)

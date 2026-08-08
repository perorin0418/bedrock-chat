# MCPサーバー登録 複数認証方式対応 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** bot単位のMCPサーバー設定に認証方式(`auth_type`)の選択を追加し、既存のCognito `client_credentials`方式に加えて、`none`(認証なし)/`bearer_token`(固定Bearerトークン)/`basic_auth`(username+token)を登録できるようにする。これによりAtlassian Rovo MCP Server(APIトークン認証)を含む、Cognito以外の認証を要求する外部MCPサーバーを登録できるようになる。

**Architecture:** `McpConfig`(APIスキーマ)/`McpConfigModel`(永続化モデル)に`auth_type`判別フィールドと、型ごとの秘密情報フィールド(`client_secret`/`bearer_token`/`basic_auth_token`)を追加する。`auth_type`省略時は既存の`cognito_client_credentials`扱いになるため、既存bot・既存DynamoDB項目・既存Secrets Manager格納形式(`{label: 秘密文字列}`のJSON blob)は無改修で動作する。Secrets Managerへの読み書きは「`auth_type`に応じて1つの秘密フィールドを読み書きする」ように既存の`_get_secret`/`_set_secret`ヘルパーを汎用化するだけで対応する。実行時(`mcp_tools.py`)は、`Authorization`ヘッダーの組み立て部分だけを`auth_type`で分岐する関数に切り出し、既存のper-server縮退運転ロジック(1サーバー失敗しても他は継続)は変更しない。フロントエンドは`McpConfig.tsx`に認証タイプの`Select`を追加し、選択に応じて入力項目を出し分ける。

**Tech Stack:** Python(FastAPI backend, pydantic v2, strands-agents SDK), TypeScript/React(frontend), unittest(backend, `poetry run python tests/...`で実行), vitest(frontend、既存コンポーネントに単体テストは無い方針を踏襲)

## Global Constraints

- 対応する`auth_type`は`cognito_client_credentials`(既存デフォルト)/`none`/`bearer_token`/`basic_auth`の4つ。OAuth 2.1対話フローはスコープ外
- `auth_type`省略時は`cognito_client_credentials`とみなす(既存bot・既存リクエストペイロードへの後方互換)
- 選択中の`auth_type`に不要なフィールドが埋まっている場合はAPI層でエラーにする(設定の取り違え防止)
- Secrets Manager格納形式(`{label: 秘密文字列}`のJSON blob、1 botにつき1つの`secret_arn`)は変更しない。CDK側の`secret:mcp/*/*`権限も無改修
- `endpoint_url`は既存通り自由入力(https必須)。Rovo固有の情報(ドメイン名、固定URL等)はコードに埋め込まない
- 参照設計書: `docs/superpowers/specs/2026-08-08-mcp-server-multi-auth-design.md`

---

### Task 1: APIスキーマに`auth_type`と型別フィールドを追加する

**Files:**
- Modify: `backend/app/routes/schemas/bot.py:140-190`(`McpConfig`/`McpTool`)
- Test: `backend/tests/test_routes/test_schemas/test_bot_mcp_config.py`(新規)

**Interfaces:**
- Produces: `McpAuthType`(`Enum`、値: `"cognito_client_credentials"` / `"none"` / `"bearer_token"` / `"basic_auth"`)、`McpConfig(label, endpoint_url, auth_type=McpAuthType.COGNITO_CLIENT_CREDENTIALS, client_id=None, client_secret=None, bearer_token=None, username=None, basic_auth_token=None)`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_routes/test_schemas/test_bot_mcp_config.py` を新規作成:

```python
import sys

sys.path.append(".")
import unittest

from pydantic import ValidationError

from app.routes.schemas.bot import McpAuthType, McpConfig


class TestMcpConfigAuthType(unittest.TestCase):
    def test_defaults_to_cognito_client_credentials(self):
        config = McpConfig(
            label="foo",
            endpoint_url="https://example.com/mcp",
            client_id="id-1",
            client_secret="secret-1",
        )
        self.assertEqual(config.auth_type, McpAuthType.COGNITO_CLIENT_CREDENTIALS)

    def test_cognito_client_credentials_requires_client_id_and_secret(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.COGNITO_CLIENT_CREDENTIALS,
            )

    def test_none_requires_no_extra_fields(self):
        config = McpConfig(
            label="foo",
            endpoint_url="https://example.com/mcp",
            auth_type=McpAuthType.NONE,
        )
        self.assertIsNone(config.client_id)
        self.assertIsNone(config.bearer_token)

    def test_none_rejects_extra_fields(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.NONE,
                bearer_token="should-not-be-here",
            )

    def test_bearer_token_requires_token(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.BEARER_TOKEN,
            )

    def test_bearer_token_valid(self):
        config = McpConfig(
            label="foo",
            endpoint_url="https://example.com/mcp",
            auth_type=McpAuthType.BEARER_TOKEN,
            bearer_token="token-1",
        )
        self.assertEqual(config.bearer_token, "token-1")

    def test_basic_auth_requires_username_and_token(self):
        with self.assertRaises(ValidationError):
            McpConfig(
                label="foo",
                endpoint_url="https://example.com/mcp",
                auth_type=McpAuthType.BASIC_AUTH,
                username="user-1",
            )

    def test_basic_auth_valid(self):
        config = McpConfig(
            label="foo",
            endpoint_url="https://example.com/mcp",
            auth_type=McpAuthType.BASIC_AUTH,
            username="user-1",
            basic_auth_token="token-1",
        )
        self.assertEqual(config.username, "user-1")
        self.assertEqual(config.basic_auth_token, "token-1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && poetry run python tests/test_routes/test_schemas/test_bot_mcp_config.py -v`
Expected: `ImportError: cannot import name 'McpAuthType' from 'app.routes.schemas.bot'`(または`ModuleNotFoundError`)でFAIL

- [ ] **Step 3: 最小実装を書く**

`backend/app/routes/schemas/bot.py`の3行目に`import re`があるので、その直後に以下を追加する:

```python
from enum import Enum
```

同ファイル26-32行目:

```python
from pydantic import (
    Discriminator,
    Field,
    create_model,
    field_validator,
    validator,
)
```

を以下に置き換える:

```python
from pydantic import (
    Discriminator,
    Field,
    create_model,
    field_validator,
    model_validator,
    validator,
)
```

`backend/app/routes/schemas/bot.py`の現在の(140-176行目):

```python
class McpConfig(BaseSchema):
    label: str
    endpoint_url: str
    client_id: str
    client_secret: str

    @field_validator("label")
    def validate_label(cls, v):
        if v == "":
            raise ValueError("MCP server label is empty")
        if len(v) > 20:
            raise ValueError("MCP server label must be 20 characters or fewer")
        if not re.fullmatch(r"[a-zA-Z0-9_]+", v):
            raise ValueError(
                "MCP server label must contain only letters, digits, and underscores"
            )
        return v

    @field_validator("endpoint_url")
    def validate_endpoint_url(cls, v):
        if v == "":
            raise ValueError("MCP endpoint URL is empty")
        if not v.startswith("https://"):
            raise ValueError("MCP endpoint URL must use https://")
        return v

    @field_validator("client_id")
    def validate_client_id(cls, v):
        if v == "":
            raise ValueError("MCP client ID is empty")
        return v

    @field_validator("client_secret")
    def validate_client_secret(cls, v):
        if v == "":
            raise ValueError("MCP client secret is empty")
        return v
```

を以下に置き換える:

```python
class McpAuthType(str, Enum):
    COGNITO_CLIENT_CREDENTIALS = "cognito_client_credentials"
    NONE = "none"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"


MCP_AUTH_REQUIRED_FIELDS: dict[McpAuthType, list[str]] = {
    McpAuthType.COGNITO_CLIENT_CREDENTIALS: ["client_id", "client_secret"],
    McpAuthType.NONE: [],
    McpAuthType.BEARER_TOKEN: ["bearer_token"],
    McpAuthType.BASIC_AUTH: ["username", "basic_auth_token"],
}
MCP_AUTH_ALL_FIELDS = {"client_id", "client_secret", "bearer_token", "username", "basic_auth_token"}


class McpConfig(BaseSchema):
    label: str
    endpoint_url: str
    auth_type: McpAuthType = McpAuthType.COGNITO_CLIENT_CREDENTIALS
    client_id: str | None = None
    client_secret: str | None = None
    bearer_token: str | None = None
    username: str | None = None
    basic_auth_token: str | None = None

    @field_validator("label")
    def validate_label(cls, v):
        if v == "":
            raise ValueError("MCP server label is empty")
        if len(v) > 20:
            raise ValueError("MCP server label must be 20 characters or fewer")
        if not re.fullmatch(r"[a-zA-Z0-9_]+", v):
            raise ValueError(
                "MCP server label must contain only letters, digits, and underscores"
            )
        return v

    @field_validator("endpoint_url")
    def validate_endpoint_url(cls, v):
        if v == "":
            raise ValueError("MCP endpoint URL is empty")
        if not v.startswith("https://"):
            raise ValueError("MCP endpoint URL must use https://")
        return v

    @model_validator(mode="after")
    def validate_auth_fields(self):
        needed = set(MCP_AUTH_REQUIRED_FIELDS[self.auth_type])
        for field in needed:
            if not getattr(self, field):
                raise ValueError(
                    f"'{field}' is required when auth_type is '{self.auth_type.value}'"
                )
        for field in MCP_AUTH_ALL_FIELDS - needed:
            if getattr(self, field):
                raise ValueError(
                    f"'{field}' must not be set when auth_type is '{self.auth_type.value}'"
                )
        return self
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && poetry run python tests/test_routes/test_schemas/test_bot_mcp_config.py -v`
Expected: 8 tests, PASS

- [ ] **Step 5: 既存の関連テストが壊れていないことを確認する**

Run: `cd backend && poetry run python tests/test_repositories/test_models/test_mcp_tool.py -v`
Expected: 既存4テストすべてPASS(既存テストは`auth_type`を指定していないため、デフォルト値`cognito_client_credentials`で動作する)

- [ ] **Step 6: 型チェック・フォーマットチェック**

Run: `cd backend && poetry run mypy --config-file mypy.ini app/routes/schemas/bot.py && poetry run black --check app/routes/schemas/bot.py`
Expected: エラーなし

- [ ] **Step 7: コミット**

```bash
git add backend/app/routes/schemas/bot.py backend/tests/test_routes/test_schemas/test_bot_mcp_config.py
git commit -m "feat: add auth_type to McpConfig schema for non-Cognito MCP servers"
```

---

### Task 2: 永続化モデルとSecrets Manager格納を`auth_type`対応にする

**Files:**
- Modify: `backend/app/repositories/models/custom_bot.py:290-381`(`McpConfigModel`/`McpToolModel`)、`:467-483`(`AgentModel.to_agent`内のMCP変換)
- Test: `backend/tests/test_repositories/test_models/test_mcp_tool.py`(追記)

**Interfaces:**
- Consumes: Task 1の`McpAuthType`、`McpConfig`(`routes/schemas/bot.py`)
- Produces: `McpConfigModel(label, endpoint_url, auth_type, client_id=None, client_secret=None, bearer_token=None, username=None, basic_auth_token=None)`、`McpConfigModel.from_mcp_config(config: McpConfig) -> McpConfigModel`(既存シグネチャ維持)、`McpToolModel.from_tool_input(tool, user_id, bot_id) -> McpToolModel`(既存シグネチャ維持)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_repositories/test_models/test_mcp_tool.py`の`TestMcpToolModel`クラスに以下を追記(既存の`test_repr_does_not_leak_client_secret`の直前に挿入):

```python
    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_stores_bearer_token_secret(self, mock_store):
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
                            label="rovo",
                            endpoint_url="https://mcp.atlassian.com/v1/mcp",
                            auth_type="bearer_token",
                            bearer_token="rovo-token-1",
                        ),
                    ],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        tool = agent_model.tools[0]
        self.assertEqual(tool.mcpServers[0].bearer_token, "rovo-token-1")
        mock_store.assert_called_once_with(
            "user1", "bot1", "mcp", json.dumps({"rovo": "rovo-token-1"})
        )

    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_stores_basic_auth_secret(self, mock_store):
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
                            label="rovo",
                            endpoint_url="https://mcp.atlassian.com/v1/mcp",
                            auth_type="basic_auth",
                            username="me@example.com",
                            basic_auth_token="api-token-1",
                        ),
                    ],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        tool = agent_model.tools[0]
        self.assertEqual(tool.mcpServers[0].username, "me@example.com")
        self.assertEqual(tool.mcpServers[0].basic_auth_token, "api-token-1")
        mock_store.assert_called_once_with(
            "user1", "bot1", "mcp", json.dumps({"rovo": "api-token-1"})
        )

    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_none_auth_type_skips_secret_storage(self, mock_store):
        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpServers=[
                        McpConfig(
                            label="public",
                            endpoint_url="https://example.com/mcp",
                            auth_type="none",
                        ),
                    ],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        tool = agent_model.tools[0]
        self.assertIsNone(tool.secret_arn)
        mock_store.assert_not_called()

    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_mixed_auth_types_only_stores_needed_secrets(
        self, mock_store
    ):
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
                            label="public",
                            endpoint_url="https://example.com/mcp",
                            auth_type="none",
                        ),
                        McpConfig(
                            label="rovo",
                            endpoint_url="https://mcp.atlassian.com/v1/mcp",
                            auth_type="bearer_token",
                            bearer_token="rovo-token-1",
                        ),
                    ],
                )
            ]
        )

        AgentModel.from_agent_input(agent_input, "user1", "bot1")

        mock_store.assert_called_once_with(
            "user1", "bot1", "mcp", json.dumps({"rovo": "rovo-token-1"})
        )

    @patch("app.repositories.models.custom_bot.get_api_key_from_secret_manager")
    def test_to_agent_round_trips_bearer_token(self, mock_get_secret):
        mock_get_secret.return_value = json.dumps({"rovo": "rovo-token-1"})

        agent_model = AgentModel(
            tools=[
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
                    mcpServers=[
                        McpConfigModel(
                            label="rovo",
                            endpoint_url="https://mcp.atlassian.com/v1/mcp",
                            auth_type="bearer_token",
                            bearer_token="",
                        ),
                    ],
                )
            ]
        )

        agent = agent_model.to_agent()

        self.assertEqual(agent.tools[0].mcpServers[0].bearer_token, "rovo-token-1")

    def test_to_agent_round_trips_none_auth_type_without_secret_lookup(self):
        agent_model = AgentModel(
            tools=[
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    secret_arn=None,
                    mcpServers=[
                        McpConfigModel(
                            label="public",
                            endpoint_url="https://example.com/mcp",
                            auth_type="none",
                        ),
                    ],
                )
            ]
        )

        agent = agent_model.to_agent()

        self.assertEqual(agent.tools[0].mcpServers[0].auth_type, "none")
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && poetry run python tests/test_repositories/test_models/test_mcp_tool.py -v`
Expected: `pydantic_core._pydantic_core.ValidationError`または`TypeError`(`auth_type`/`bearer_token`等の未知フィールド、または`McpConfig(auth_type=...)`が受け付けられない)でFAIL

- [ ] **Step 3: 最小実装を書く**

`backend/app/repositories/models/custom_bot.py`の290-381行目を以下に置き換える:

```python
def _mcp_secret_field_name(auth_type: McpAuthType) -> str | None:
    return {
        McpAuthType.COGNITO_CLIENT_CREDENTIALS: "client_secret",
        McpAuthType.BEARER_TOKEN: "bearer_token",
        McpAuthType.BASIC_AUTH: "basic_auth_token",
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
        )


class McpToolModel(BaseModel):
    tool_type: Literal["mcp"] = Field(
        "mcp",
        description="Type of tool. It does need additional settings for the MCP server connection.",
    )
    name: str
    description: str
    mcpServers: list[McpConfigModel] = []
    secret_arn: str | None = None

    @model_validator(mode="before")
    @classmethod
    def load_mcp_secrets(cls, data):
        """Load per-server secrets from the shared Secrets Manager entry when empty.

        All of a bot's MCP server secrets are stored together as one JSON blob
        (`{label: secret_value, ...}`) under a single Secrets Manager entry
        (`secret_arn`), keyed by each server's `label`. Which field holds the
        secret depends on that server's `auth_type` (see `_mcp_secret_field_name`);
        servers with `auth_type == "none"` have no secret and are skipped.
        """
        if isinstance(data, dict) and data.get("mcpServers") and data.get("secret_arn"):
            servers = data["mcpServers"]

            def _get_auth_type(s):
                raw = s.get("auth_type") if isinstance(s, dict) else s.auth_type
                return McpAuthType(raw) if raw else McpAuthType.COGNITO_CLIENT_CREDENTIALS

            def _get_secret(s):
                field = _mcp_secret_field_name(_get_auth_type(s))
                if field is None:
                    return None
                return s.get(field, "") if isinstance(s, dict) else getattr(s, field)

            def _get_label(s):
                return s.get("label") if isinstance(s, dict) else s.label

            def _set_secret(s, value):
                field = _mcp_secret_field_name(_get_auth_type(s))
                if field is None:
                    return
                if isinstance(s, dict):
                    s[field] = value
                else:
                    setattr(s, field, value)

            needs_load = any(_get_secret(s) == "" for s in servers)
            if needs_load:
                try:
                    secrets_by_label = json.loads(
                        get_api_key_from_secret_manager(data["secret_arn"])
                    )
                except Exception as e:
                    logger.error(f"Failed to retrieve MCP secrets from ARN: {e}")
                    raise ValueError(
                        f"Failed to retrieve MCP secrets from ARN: {data['secret_arn']}"
                    )
                for s in servers:
                    if _get_secret(s) == "":
                        _set_secret(s, secrets_by_label.get(_get_label(s), ""))
        return data

    @classmethod
    def from_tool_input(cls, tool: McpTool, user_id: str, bot_id: str) -> Self:
        secrets_by_label = {}
        for server in tool.mcpServers:
            field = _mcp_secret_field_name(server.auth_type)
            if field is not None:
                secrets_by_label[server.label] = getattr(server, field)

        secret_arn = None
        if secrets_by_label:
            secret_arn = store_api_key_to_secret_manager(
                user_id, bot_id, "mcp", json.dumps(secrets_by_label)
            )

        servers = [McpConfigModel.from_mcp_config(server) for server in tool.mcpServers]

        return cls(
            tool_type="mcp",
            name=tool.name,
            description=tool.description,
            mcpServers=servers,
            secret_arn=secret_arn,
        )
```

`AgentModel.to_agent`内(元467-483行目)のMCP変換部分を以下に置き換える:

```python
            elif isinstance(tool, McpToolModel):
                tools.append(
                    McpTool(
                        tool_type="mcp",
                        name=tool.name,
                        description=tool.description,
                        mcpServers=[
                            McpConfig(
                                label=server.label,
                                endpoint_url=server.endpoint_url,
                                auth_type=server.auth_type,
                                client_id=server.client_id,
                                client_secret=server.client_secret,
                                bearer_token=server.bearer_token,
                                username=server.username,
                                basic_auth_token=server.basic_auth_token,
                            )
                            for server in tool.mcpServers
                        ],
                    )
                )
```

`backend/app/repositories/models/custom_bot.py`の20-44行目、`from app.routes.schemas.bot import (`の`Knowledge,`の直後(37行目、`McpConfig,`の直前)に`McpAuthType,`を追加する:

```python
    Knowledge,
    McpAuthType,
    McpConfig,
    McpTool,
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && poetry run python tests/test_repositories/test_models/test_mcp_tool.py -v`
Expected: 全テスト(既存4件+新規6件)PASS

- [ ] **Step 5: 型チェック・フォーマットチェック**

Run: `cd backend && poetry run mypy --config-file mypy.ini app/repositories/models/custom_bot.py && poetry run black --check app/repositories/models/custom_bot.py`
Expected: エラーなし

- [ ] **Step 6: コミット**

```bash
git add backend/app/repositories/models/custom_bot.py backend/tests/test_repositories/test_models/test_mcp_tool.py
git commit -m "feat: store per-auth-type secrets for MCP server configs"
```

---

### Task 3: 実行時の認証ヘッダー構築を`auth_type`で分岐する

**Files:**
- Modify: `backend/app/strands_integration/tools/mcp_tools.py`(L5-13のimport、L49-79直後への追加、L154-171の`mcp_tools_scope`内ヘッダー構築部分)
- Test: `backend/tests/test_strands_integration/test_mcp_tools.py`(追記)

**Interfaces:**
- Consumes: Task 2の`McpConfigModel`(`auth_type`, `client_id`, `client_secret`, `bearer_token`, `username`, `basic_auth_token`)
- Produces: `_build_auth_headers(server: McpConfigModel, secret_arn: str | None) -> dict[str, str]`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_strands_integration/test_mcp_tools.py`の`TestGetMcpBearerToken`クラスの直後(88行目の空行部分)に以下を追記し、importに`McpAuthType`と`_build_auth_headers`を追加する:

```python
from app.repositories.models.custom_bot import McpAuthType, McpConfigModel
from app.strands_integration.tools.mcp_tools import _build_auth_headers


class TestBuildAuthHeaders(unittest.TestCase):
    def test_none_returns_no_headers(self):
        server = McpConfigModel(
            label="public",
            endpoint_url="https://example.com/mcp",
            auth_type=McpAuthType.NONE,
        )

        headers = _build_auth_headers(server, secret_arn=None)

        self.assertEqual(headers, {})

    def test_bearer_token_returns_bearer_header(self):
        server = McpConfigModel(
            label="rovo",
            endpoint_url="https://mcp.atlassian.com/v1/mcp",
            auth_type=McpAuthType.BEARER_TOKEN,
            bearer_token="rovo-token-1",
        )

        headers = _build_auth_headers(server, secret_arn=None)

        self.assertEqual(headers, {"Authorization": "Bearer rovo-token-1"})

    def test_basic_auth_returns_base64_basic_header(self):
        server = McpConfigModel(
            label="rovo",
            endpoint_url="https://mcp.atlassian.com/v1/mcp",
            auth_type=McpAuthType.BASIC_AUTH,
            username="me@example.com",
            basic_auth_token="api-token-1",
        )

        headers = _build_auth_headers(server, secret_arn=None)

        import base64

        expected = base64.b64encode(b"me@example.com:api-token-1").decode()
        self.assertEqual(headers, {"Authorization": f"Basic {expected}"})

    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_cognito_client_credentials_uses_existing_token_flow(
        self, mock_get_token
    ):
        mock_get_token.return_value = "cognito-token-1"
        server = McpConfigModel(
            label="powersort",
            endpoint_url="https://example.com/powersort",
            auth_type=McpAuthType.COGNITO_CLIENT_CREDENTIALS,
            client_id="client-1",
            client_secret="secret-1",
        )

        headers = _build_auth_headers(server, secret_arn="arn:aws:secretsmanager:...")

        self.assertEqual(headers, {"Authorization": "Bearer cognito-token-1"})
        mock_get_token.assert_called_once_with(
            "client-1",
            "secret-1",
            COGNITO_MCP_AUTH_DOMAIN,
            "arn:aws:secretsmanager:...:powersort",
        )
```

`backend/tests/test_strands_integration/test_mcp_tools.py`の12-16行目、既存の

```python
from app.strands_integration.tools.mcp_tools import (
    _LabelStrippingMcpClient,
    _token_cache,
    get_mcp_bearer_token,
)
```

を以下に置き換える:

```python
from app.strands_integration.tools.mcp_tools import (
    COGNITO_MCP_AUTH_DOMAIN,
    _LabelStrippingMcpClient,
    _build_auth_headers,
    _token_cache,
    get_mcp_bearer_token,
)
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd backend && poetry run python tests/test_strands_integration/test_mcp_tools.py -v`
Expected: `ImportError: cannot import name '_build_auth_headers'`でFAIL

- [ ] **Step 3: 最小実装を書く**

`backend/app/strands_integration/tools/mcp_tools.py`の5-13行目:

```python
import logging
import os
import time
from contextlib import ExitStack, contextmanager

import requests
from app.repositories.models.custom_bot import BotModel, McpToolModel
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPAgentTool, MCPClient
```

を以下に置き換える:

```python
import base64
import logging
import os
import time
from contextlib import ExitStack, contextmanager

import requests
from app.repositories.models.custom_bot import (
    BotModel,
    McpAuthType,
    McpConfigModel,
    McpToolModel,
)
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPAgentTool, MCPClient
```

`get_mcp_bearer_token`関数(49-79行目)の直後に以下を追加:

```python
def _build_auth_headers(server: McpConfigModel, secret_arn: str | None) -> dict[str, str]:
    """Build the `Authorization` header (if any) for connecting to one MCP server,
    based on that server's configured `auth_type`."""
    if server.auth_type == McpAuthType.NONE:
        return {}

    if server.auth_type == McpAuthType.BEARER_TOKEN:
        return {"Authorization": f"Bearer {server.bearer_token}"}

    if server.auth_type == McpAuthType.BASIC_AUTH:
        credentials = base64.b64encode(
            f"{server.username}:{server.basic_auth_token}".encode()
        ).decode()
        return {"Authorization": f"Basic {credentials}"}

    # McpAuthType.COGNITO_CLIENT_CREDENTIALS (existing behavior)
    token = get_mcp_bearer_token(
        server.client_id,
        server.client_secret,
        COGNITO_MCP_AUTH_DOMAIN,
        f"{secret_arn}:{server.label}",
    )
    return {"Authorization": f"Bearer {token}"}
```

`mcp_tools_scope`内の157-169行目:

```python
                token = get_mcp_bearer_token(
                    server.client_id,
                    server.client_secret,
                    COGNITO_MCP_AUTH_DOMAIN,
                    f"{mcp_tool.secret_arn}:{server.label}",
                )
                client = MCPClient(
                    lambda: streamablehttp_client(
                        server.endpoint_url,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=MCP_CONNECTION_TIMEOUT_SECONDS,
                    ),
                    startup_timeout=MCP_CONNECTION_TIMEOUT_SECONDS,
                )
                client.__enter__()
```

を以下に置き換える:

```python
                headers = _build_auth_headers(server, mcp_tool.secret_arn)
                client = MCPClient(
                    lambda headers=headers: streamablehttp_client(
                        server.endpoint_url,
                        headers=headers,
                        timeout=MCP_CONNECTION_TIMEOUT_SECONDS,
                    ),
                    startup_timeout=MCP_CONNECTION_TIMEOUT_SECONDS,
                )
                client.__enter__()
```

(`lambda headers=headers: ...`はループ変数`headers`をクロージャに束縛するためのデフォルト引数トリック。既存コードは`server`/`token`をループ内で使い切っており同種の問題は無いが、`headers`はループ内で毎回再代入される変数なので明示的に束縛する)

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd backend && poetry run python tests/test_strands_integration/test_mcp_tools.py -v`
Expected: 全テスト(既存分含む)PASS

- [ ] **Step 5: 型チェック・フォーマットチェック**

Run: `cd backend && poetry run mypy --config-file mypy.ini app/strands_integration/tools/mcp_tools.py && poetry run black --check app/strands_integration/tools/mcp_tools.py`
Expected: エラーなし

- [ ] **Step 6: コミット**

```bash
git add backend/app/strands_integration/tools/mcp_tools.py backend/tests/test_strands_integration/test_mcp_tools.py
git commit -m "feat: branch MCP auth header construction on auth_type"
```

---

### Task 4: フロントエンド型定義を`authType`対応にする

**Files:**
- Modify: `frontend/src/features/agent/types/index.d.ts:23-28`

**Interfaces:**
- Produces: `McpAuthType`(`'cognito_client_credentials' | 'none' | 'bearer_token' | 'basic_auth'`)、`McpConfig(label, endpointUrl, authType, clientId?, clientSecret?, bearerToken?, username?, basicAuthToken?)`

- [ ] **Step 1: 型定義を変更する**

`frontend/src/features/agent/types/index.d.ts`の23-28行目:

```ts
export type McpConfig = {
  label: string;
  endpointUrl: string;
  clientId: string;
  clientSecret: string;
};
```

を以下に置き換える:

```ts
export type McpAuthType =
  | 'cognito_client_credentials'
  | 'none'
  | 'bearer_token'
  | 'basic_auth';

export type McpConfig = {
  label: string;
  endpointUrl: string;
  authType: McpAuthType;
  clientId?: string;
  clientSecret?: string;
  bearerToken?: string;
  username?: string;
  basicAuthToken?: string;
};
```

- [ ] **Step 2: 型チェックを実行する(この時点ではまだ他ファイルでエラーが出る)**

Run: `cd frontend && npx tsc --noEmit`
Expected: `McpServersConfig.tsx`の`EMPTY_SERVER`で`authType`が無い旨のエラーが出る(Task 6で解消するため、ここでは想定通り)

- [ ] **Step 3: コミット**

```bash
git add frontend/src/features/agent/types/index.d.ts
git commit -m "feat: add authType to frontend McpConfig type"
```

---

### Task 5: `McpConfig.tsx`に認証タイプ選択UIを追加する

**Files:**
- Modify: `frontend/src/features/agent/components/McpConfig.tsx`(全体)
- Modify: `frontend/src/i18n/ja/index.ts:293-310`(`mcpConfig`セクション)
- Modify: `frontend/src/i18n/en/index.ts`(同セクション)

**Interfaces:**
- Consumes: Task 4の`McpConfig`/`McpAuthType`型

- [ ] **Step 1: i18nキーを追加する**

`frontend/src/i18n/ja/index.ts`の293-310行目(`mcpConfig`セクション)を以下に置き換える:

```ts
        mcpConfig: {
          authType: {
            label: '認証方式',
            options: {
              cognito_client_credentials: 'ボット間ナレッジ共有(Cognito)',
              none: '認証なし',
              bearer_token: 'Bearerトークン',
              basic_auth: 'Basic認証(ユーザー名+トークン)',
            },
          },
          label: {
            label: 'サーバーラベル',
            placeholder: '英数字とアンダースコアのみ(例: powersort)',
          },
          endpointUrl: {
            label: 'エンドポイントURL',
            placeholder: 'MCPサーバーのエンドポイントURLを入力',
          },
          clientId: {
            label: 'Client ID',
            placeholder: 'Client IDを入力',
          },
          clientSecret: {
            label: 'Client Secret',
            placeholder: 'Client Secretを入力',
          },
          bearerToken: {
            label: 'トークン',
            placeholder: 'Bearerトークンを入力',
          },
          username: {
            label: 'ユーザー名',
            placeholder: 'ユーザー名(またはメールアドレス)を入力',
          },
          basicAuthToken: {
            label: 'トークン',
            placeholder: 'APIトークンを入力',
          },
        },
```

`frontend/src/i18n/en/index.ts`の対応する`mcpConfig`セクションを以下に置き換える:

```ts
        mcpConfig: {
          authType: {
            label: 'Authentication Type',
            options: {
              cognito_client_credentials: 'Bot-to-bot knowledge sharing (Cognito)',
              none: 'No authentication',
              bearer_token: 'Bearer token',
              basic_auth: 'Basic auth (username + token)',
            },
          },
          label: {
            label: 'Server Label',
            placeholder: 'Letters, digits, and underscores only (e.g. powersort)',
          },
          endpointUrl: {
            label: 'Endpoint URL',
            placeholder: 'Enter the MCP server endpoint URL',
          },
          clientId: {
            label: 'Client ID',
            placeholder: 'Enter Client ID',
          },
          clientSecret: {
            label: 'Client Secret',
            placeholder: 'Enter Client Secret',
          },
          bearerToken: {
            label: 'Token',
            placeholder: 'Enter the bearer token',
          },
          username: {
            label: 'Username',
            placeholder: 'Enter username (or email)',
          },
          basicAuthToken: {
            label: 'Token',
            placeholder: 'Enter the API token',
          },
        },
```

- [ ] **Step 2: `McpConfig.tsx`に認証タイプSelectと条件表示を実装する**

`frontend/src/features/agent/components/McpConfig.tsx`全体を以下に置き換える:

```tsx
import { useTranslation } from 'react-i18next';
import InputText from '../../../components/InputText';
import Select from '../../../components/Select';
import { McpAuthType, McpConfig as McpConfigType } from '../types';

type Props = {
  config: McpConfigType;
  onChange: (config: McpConfigType) => void;
};

const AUTH_TYPES: McpAuthType[] = [
  'cognito_client_credentials',
  'none',
  'bearer_token',
  'basic_auth',
];

export const McpConfig = ({ config, onChange }: Props) => {
  const { t } = useTranslation();

  const authTypeOptions = AUTH_TYPES.map((authType) => ({
    value: authType,
    label: t(`agent.tools.mcpConfig.authType.options.${authType}`),
  }));

  return (
    <div className="space-y-4">
      <InputText
        label={t('agent.tools.mcpConfig.label.label')}
        placeholder={t('agent.tools.mcpConfig.label.placeholder')}
        value={config.label}
        maxLength={20}
        onChange={(value) => onChange({ ...config, label: value })}
      />
      <InputText
        label={t('agent.tools.mcpConfig.endpointUrl.label')}
        placeholder={t('agent.tools.mcpConfig.endpointUrl.placeholder')}
        value={config.endpointUrl}
        onChange={(value) => onChange({ ...config, endpointUrl: value })}
      />
      <Select
        label={t('agent.tools.mcpConfig.authType.label')}
        value={config.authType}
        options={authTypeOptions}
        onChange={(value) =>
          onChange({
            ...config,
            authType: value as McpAuthType,
            clientId: undefined,
            clientSecret: undefined,
            bearerToken: undefined,
            username: undefined,
            basicAuthToken: undefined,
          })
        }
      />
      {config.authType === 'cognito_client_credentials' && (
        <>
          <InputText
            label={t('agent.tools.mcpConfig.clientId.label')}
            placeholder={t('agent.tools.mcpConfig.clientId.placeholder')}
            value={config.clientId ?? ''}
            onChange={(value) => onChange({ ...config, clientId: value })}
          />
          <InputText
            type="password"
            label={t('agent.tools.mcpConfig.clientSecret.label')}
            placeholder={t('agent.tools.mcpConfig.clientSecret.placeholder')}
            value={config.clientSecret ?? ''}
            onChange={(value) => onChange({ ...config, clientSecret: value })}
          />
        </>
      )}
      {config.authType === 'bearer_token' && (
        <InputText
          type="password"
          label={t('agent.tools.mcpConfig.bearerToken.label')}
          placeholder={t('agent.tools.mcpConfig.bearerToken.placeholder')}
          value={config.bearerToken ?? ''}
          onChange={(value) => onChange({ ...config, bearerToken: value })}
        />
      )}
      {config.authType === 'basic_auth' && (
        <>
          <InputText
            label={t('agent.tools.mcpConfig.username.label')}
            placeholder={t('agent.tools.mcpConfig.username.placeholder')}
            value={config.username ?? ''}
            onChange={(value) => onChange({ ...config, username: value })}
          />
          <InputText
            type="password"
            label={t('agent.tools.mcpConfig.basicAuthToken.label')}
            placeholder={t('agent.tools.mcpConfig.basicAuthToken.placeholder')}
            value={config.basicAuthToken ?? ''}
            onChange={(value) => onChange({ ...config, basicAuthToken: value })}
          />
        </>
      )}
    </div>
  );
};
```

- [ ] **Step 3: 型チェック(この時点でも`McpServersConfig.tsx`のエラーは残る)**

Run: `cd frontend && npx tsc --noEmit`
Expected: Task 4と同じ`McpServersConfig.tsx`のエラーのみ残っている

- [ ] **Step 4: コミット**

```bash
git add frontend/src/features/agent/components/McpConfig.tsx frontend/src/i18n/ja/index.ts frontend/src/i18n/en/index.ts
git commit -m "feat: add auth type selector to MCP server config form"
```

---

### Task 6: `McpServersConfig.tsx`のデフォルト値と保存前バリデーションを更新する

**Files:**
- Modify: `frontend/src/features/agent/components/McpServersConfig.tsx:13-18`(`EMPTY_SERVER`)
- Modify: `frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx:599-654`(`isToolValid`内のMCPバリデーション)
- Modify: `frontend/src/i18n/ja/index.ts`, `frontend/src/i18n/en/index.ts`(`agent.tools.mcp.error.requiredFields`の文言)

**Interfaces:**
- Consumes: Task 4の`McpConfig`/`McpAuthType`型

- [ ] **Step 1: `EMPTY_SERVER`に`authType`のデフォルト値を追加する**

`frontend/src/features/agent/components/McpServersConfig.tsx`の13-18行目:

```ts
const EMPTY_SERVER: McpConfigType = {
  label: '',
  endpointUrl: '',
  clientId: '',
  clientSecret: '',
};
```

を以下に置き換える:

```ts
const EMPTY_SERVER: McpConfigType = {
  label: '',
  endpointUrl: '',
  authType: 'cognito_client_credentials',
};
```

- [ ] **Step 2: 型チェックを実行する(この時点でエラーが解消していることを確認)**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラーなし(Task 4/5から持ち越していたエラーも含めて解消)

- [ ] **Step 3: `requiredFields`エラー文言を認証方式に依存しない汎用表現にする**

`frontend/src/i18n/ja/index.ts`の`agent.tools.mcp.error.requiredFields`:

```ts
            requiredFields: '全てのMCPサーバー設定項目(ラベル、エンドポイントURL、Client ID、Client Secret)を入力してください。',
```

を以下に置き換える:

```ts
            requiredFields: 'ラベル・エンドポイントURL、および選択した認証方式に必要な項目を全て入力してください。',
```

`frontend/src/i18n/en/index.ts`の対応行:

```ts
            requiredFields: 'Please fill in all MCP server settings (label, endpoint URL, Client ID, Client Secret).',
```

を以下に置き換える:

```ts
            requiredFields: 'Please fill in the label, endpoint URL, and all fields required by the selected authentication type.',
```

- [ ] **Step 4: `isToolValid`のMCPバリデーションを`authType`対応にする**

`frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx`の599-654行目:

```ts
      // Mcp tool validation: every configured server must have all fields filled,
      // and labels must be unique within the bot.
      if (isMcpTool(tool)) {
        const labels = tool.mcpServers.map((server) => server.label);
        const hasDuplicateLabel = labels.some(
          (label, labelIdx) => label !== '' && labels.indexOf(label) !== labelIdx
        );

        if (hasDuplicateLabel) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.duplicateLabel')
          );
          return true;
        }

        const hasInvalidServer = tool.mcpServers.some(
          (server) =>
            !server.label ||
            !server.endpointUrl ||
            !server.clientId ||
            !server.clientSecret
        );

        if (hasInvalidServer) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.requiredFields')
          );
          return true;
        }

        const hasInvalidLabelPattern = tool.mcpServers.some(
          (server) => !/^[a-zA-Z0-9_]+$/.test(server.label)
        );

        if (hasInvalidLabelPattern) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.invalidLabelPattern')
          );
          return true;
        }

        const hasTooLongLabel = tool.mcpServers.some(
          (server) => server.label.length > 20
        );

        if (hasTooLongLabel) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.labelTooLong')
          );
          return true;
        }
      }
```

を以下に置き換える:

```ts
      // Mcp tool validation: every configured server must have all fields filled,
      // and labels must be unique within the bot.
      if (isMcpTool(tool)) {
        const labels = tool.mcpServers.map((server) => server.label);
        const hasDuplicateLabel = labels.some(
          (label, labelIdx) => label !== '' && labels.indexOf(label) !== labelIdx
        );

        if (hasDuplicateLabel) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.duplicateLabel')
          );
          return true;
        }

        const requiredFieldsByAuthType: Record<
          McpAuthType,
          (keyof McpConfigType)[]
        > = {
          cognito_client_credentials: ['clientId', 'clientSecret'],
          none: [],
          bearer_token: ['bearerToken'],
          basic_auth: ['username', 'basicAuthToken'],
        };

        const hasInvalidServer = tool.mcpServers.some(
          (server) =>
            !server.label ||
            !server.endpointUrl ||
            requiredFieldsByAuthType[server.authType].some(
              (field) => !server[field]
            )
        );

        if (hasInvalidServer) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.requiredFields')
          );
          return true;
        }

        const hasInvalidLabelPattern = tool.mcpServers.some(
          (server) => !/^[a-zA-Z0-9_]+$/.test(server.label)
        );

        if (hasInvalidLabelPattern) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.invalidLabelPattern')
          );
          return true;
        }

        const hasTooLongLabel = tool.mcpServers.some(
          (server) => server.label.length > 20
        );

        if (hasTooLongLabel) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.labelTooLong')
          );
          return true;
        }
      }
```

`BotKbEditPage.tsx`のimportに`McpAuthType`と`McpConfig as McpConfigType`を追加する(30行目の`import { AgentTool } from '../../../features/agent/types';`を`import { AgentTool, McpAuthType, McpConfig as McpConfigType } from '../../../features/agent/types';`に置き換える)。

- [ ] **Step 5: 型チェック・lintを実行する**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: エラーなし、`--max-warnings 0`のlintも通る

- [ ] **Step 6: フロントエンドをビルドして確認する**

Run: `cd frontend && npm run build`
Expected: ビルド成功

- [ ] **Step 7: コミット**

```bash
git add frontend/src/features/agent/components/McpServersConfig.tsx frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx frontend/src/i18n/ja/index.ts frontend/src/i18n/en/index.ts
git commit -m "feat: validate MCP server config fields per auth type in bot edit form"
```

---

### Task 7: ドキュメントにMCPサーバー登録方法(認証方式一覧)を追記する

**Files:**
- Modify: `docs/AGENT.md`

**Interfaces:**
- (ドキュメントのみ、コード変更なし)

- [ ] **Step 1: `docs/AGENT.md`の末尾にMCPサーバー登録セクションを追記する**

`docs/AGENT.md`の内容を確認し、既存の見出し構成に合わせて以下のセクションを追記する:

```markdown
## MCPサーバーの登録(bot単位)

bot作成/編集画面の「MCPサーバー」ツールで、外部のMCPサーバーを登録できる。サーバーごとに以下4つの認証方式から選択する:

| 認証方式 | 用途 | 必要な入力項目 |
|---|---|---|
| ボット間ナレッジ共有(Cognito) | 自社の他botが公開するナレッジベースMCPサーバーに接続する | Client ID, Client Secret |
| 認証なし | 認証を要求しない公開MCPサーバーに接続する | (URLのみ) |
| Bearerトークン | 固定トークンを`Authorization: Bearer <token>`で送るMCPサーバーに接続する | トークン |
| Basic認証(ユーザー名+トークン) | `Authorization: Basic <base64>`を要求するMCPサーバーに接続する | ユーザー名, トークン |

### 例: Atlassian Rovo MCP Serverを登録する

Atlassian Rovo MCP Server(Jira/Confluence等に接続するAtlassian公式のMCPサーバー)は、組織管理者がAPIトークン認証を有効化していれば、以下のいずれかで登録できる:

- サービスアカウントAPIキーを使う場合: 認証方式に「Bearerトークン」を選択し、エンドポイントURLに`https://mcp.atlassian.com/v1/mcp`、トークンにサービスアカウントのAPIキーを入力する
- 個人APIトークンを使う場合: 認証方式に「Basic認証」を選択し、エンドポイントURLに`https://mcp.atlassian.com/v1/mcp`、ユーザー名にAtlassianアカウントのメールアドレス、トークンに個人APIトークンを入力する

APIトークン認証で利用できないツール(Compass等)がある点、トークンがcloud IDに紐付いていないためツール呼び出し時にcloud IDを明示する必要がある点は、Atlassian公式ドキュメントを参照すること。OAuth 2.1による対話的な認可フローは本機能の対象外。
```

- [ ] **Step 2: コミット**

```bash
git add docs/AGENT.md
git commit -m "docs: document MCP server auth types with a Rovo registration example"
```

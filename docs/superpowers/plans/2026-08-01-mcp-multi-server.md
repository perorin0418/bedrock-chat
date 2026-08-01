# 1 bot = n MCPサーバー対応 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 既存のMCPツール機能(1 bot = 1 MCPサーバー)を拡張し、1つのbotに複数のMCPサーバーを登録・同時接続できるようにする。

**Architecture:** `McpConfig`/`McpConfigModel` に `label`(サーバー識別用・ツール名prefix用)を追加し、単一の `mcpConfig` を `mcpServers: list[...]` に変更する。Client Secretはbot単位で1つのSecrets Managerシークレットに `{label: secret, ...}` のJSONとしてまとめて保存する(既存の `store_api_key_to_secret_manager`/`get_api_key_from_secret_manager` をそのまま再利用)。ランタイムでは `mcp_tools_scope` がサーバーごとに独立して接続・失敗処理を行い(`contextlib.ExitStack` で複数接続を管理)、各サーバーが返すツール名の先頭に `{label}_` を付与して結合する。フロントエンドは単一入力フォームをリスト形式(追加/削除可能)に変更する。

**Tech Stack:** Python(FastAPI backend, strands-agents SDK, pydantic v2), TypeScript/React(frontend), pytest

## Global Constraints

- 1 botにつきMCPサーバーは0〜n個。上限は設けない
- サーバーラベル(`label`)は英数字とアンダースコアのみ、bot内で一意。ツール名prefixとして使うため
- ツール名衝突回避: 各MCPサーバーが返すツールの名前に `{label}_` を接頭辞として付与する。付与方法は `MCPAgentTool.mcp_tool.name`(strands SDKの `mcp.types.Tool.name`、pydantic mutable field)を直接書き換える。`tool_name`/`tool_spec["name"]` はこの値を参照するプロパティなので、書き換えれば自動的に反映される(実装前にこの前提を検証済み: `poetry run python -c "from mcp.types import Tool as MCPTool; t = MCPTool(name='search', description='d', inputSchema={'type':'object'}); t.name = 'x_search'; print(t.name)"` → `x_search`)
- 1サーバーへの接続・トークン取得・`list_tools_sync()`のいずれかが失敗しても、そのサーバーのツールのみ利用不可になり、他のサーバーのツール・チャット自体は継続する(縮退運転はサーバー単位)
- Client Secretの保存は既存の `store_api_key_to_secret_manager`/`get_api_key_from_secret_manager`(`backend/app/utils.py`)をそのまま再利用する。値はサーバーlabelをキーとしたJSON文字列。bot削除時のクリーンアップは既存の `delete_api_key_from_secret_manager(user_id, bot_id, "mcp")` 1回呼び出しのまま変更不要
- 参照設計書: `docs/superpowers/specs/2026-08-01-mcp-server-integration-design.md`(更新済み)

---

### Task 1: MCPツールAPIスキーマの複数サーバー対応

**Files:**
- Modify: `backend/app/routes/schemas/bot.py`

**Interfaces:**
- Produces: `McpConfig`(`label: str`, `endpoint_url: str`, `client_id: str`, `client_secret: str`)、`McpTool`(`mcpServers: list[McpConfig] = []`)

- [ ] **Step 1: `McpConfig` に `label` を追加し、`McpTool` をリスト対応にする**

`backend/app/routes/schemas/bot.py` の現在の

```python
class McpConfig(BaseSchema):
    endpoint_url: str
    client_id: str
    client_secret: str

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


class McpTool(BaseSchema):
    tool_type: Literal["mcp"] = "mcp"
    name: str
    description: str
    mcpConfig: Optional[McpConfig] | None = None
```

を以下に置き換える:

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
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
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


class McpTool(BaseSchema):
    tool_type: Literal["mcp"] = "mcp"
    name: str
    description: str
    mcpServers: list[McpConfig] = []

    @field_validator("mcpServers")
    def validate_unique_labels(cls, v):
        labels = [server.label for server in v]
        if len(labels) != len(set(labels)):
            raise ValueError("MCP server labels must be unique")
        return v
```

ファイル冒頭に `re` モジュールのimportがなければ追加する(`import re` を他のimportと同じ場所に追加。既存のimport文の並びを確認し、標準ライブラリのimportとして先頭付近に置く)。

- [ ] **Step 2: 動作確認**

Run: `cd backend && poetry run python -c "from app.routes.schemas.bot import McpConfig, McpTool; McpTool(tool_type='mcp', name='mcp', description='d', mcpServers=[McpConfig(label='a', endpoint_url='https://x', client_id='c', client_secret='s'), McpConfig(label='a', endpoint_url='https://y', client_id='c2', client_secret='s2')])"`
Expected: `ValidationError`(ラベル重複で失敗すること)

Run: `cd backend && poetry run python -c "from app.routes.schemas.bot import McpConfig; McpConfig(label='bad label', endpoint_url='https://x', client_id='c', client_secret='s')"`
Expected: `ValidationError`(ラベルにスペースが含まれ英数字+アンダースコアのパターンに合わないため失敗)

- [ ] **Step 3: Commit**

```bash
git add backend/app/routes/schemas/bot.py
git commit -m "feat: support multiple MCP servers per bot in API schema"
```

---

### Task 2: MCPリポジトリモデルの複数サーバー・共有シークレット対応

**Files:**
- Modify: `backend/app/repositories/models/custom_bot.py`
- Modify: `backend/tests/test_repositories/test_models/test_mcp_tool.py`(全面書き換え)

**Interfaces:**
- Consumes: `McpConfig`, `McpTool`(Task 1)
- Produces: `McpConfigModel`(`label: str`, `endpoint_url: str`, `client_id: str`, `client_secret: SecureString`)、`McpToolModel`(`mcpServers: list[McpConfigModel] = []`, `secret_arn: str | None = None`)

- [ ] **Step 1: 失敗するテストを書く(既存テストファイルを全面書き換え)**

`backend/tests/test_repositories/test_models/test_mcp_tool.py` を以下で置き換える:

```python
import sys

sys.path.append(".")
import json
import unittest
from unittest.mock import patch

from app.repositories.models.custom_bot import AgentModel, McpConfigModel, McpToolModel
from app.routes.schemas.bot import AgentInput, McpConfig, McpTool


class TestMcpToolModel(unittest.TestCase):
    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_stores_all_secrets_as_one_json_blob(self, mock_store):
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
                            label="powersort",
                            endpoint_url="https://example.com/powersort",
                            client_id="client-1",
                            client_secret="secret-1",
                        ),
                        McpConfig(
                            label="dbplayer",
                            endpoint_url="https://example.com/dbplayer",
                            client_id="client-2",
                            client_secret="secret-2",
                        ),
                    ],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        self.assertEqual(len(agent_model.tools), 1)
        tool = agent_model.tools[0]
        self.assertIsInstance(tool, McpToolModel)
        self.assertEqual(len(tool.mcpServers), 2)
        self.assertEqual(tool.mcpServers[0].label, "powersort")
        self.assertEqual(tool.mcpServers[0].client_secret, "secret-1")
        self.assertEqual(tool.mcpServers[1].label, "dbplayer")
        self.assertEqual(tool.mcpServers[1].client_secret, "secret-2")
        self.assertEqual(
            tool.secret_arn,
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
        )
        mock_store.assert_called_once_with(
            "user1",
            "bot1",
            "mcp",
            json.dumps({"powersort": "secret-1", "dbplayer": "secret-2"}),
        )

    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_with_no_servers_skips_secret_storage(self, mock_store):
        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpServers=[],
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        tool = agent_model.tools[0]
        self.assertEqual(tool.mcpServers, [])
        self.assertIsNone(tool.secret_arn)
        mock_store.assert_not_called()

    @patch("app.repositories.models.custom_bot.get_api_key_from_secret_manager")
    def test_to_agent_round_trips_all_servers(self, mock_get_secret):
        mock_get_secret.return_value = json.dumps(
            {"powersort": "secret-1", "dbplayer": "secret-2"}
        )

        agent_model = AgentModel(
            tools=[
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
                    mcpServers=[
                        McpConfigModel(
                            label="powersort",
                            endpoint_url="https://example.com/powersort",
                            client_id="client-1",
                            client_secret="",
                        ),
                        McpConfigModel(
                            label="dbplayer",
                            endpoint_url="https://example.com/dbplayer",
                            client_id="client-2",
                            client_secret="",
                        ),
                    ],
                )
            ]
        )

        agent = agent_model.to_agent()

        self.assertEqual(len(agent.tools), 1)
        tool = agent.tools[0]
        self.assertEqual(tool.tool_type, "mcp")
        self.assertEqual(len(tool.mcpServers), 2)
        self.assertEqual(tool.mcpServers[0].label, "powersort")
        self.assertEqual(tool.mcpServers[0].client_secret, "secret-1")
        self.assertEqual(tool.mcpServers[1].label, "dbplayer")
        self.assertEqual(tool.mcpServers[1].client_secret, "secret-2")

    def test_repr_does_not_leak_client_secret(self):
        model = McpConfigModel(
            label="powersort",
            endpoint_url="https://example.com/mcp",
            client_id="client-1",
            client_secret="do-not-leak-this-secret",
        )

        self.assertNotIn("do-not-leak-this-secret", repr(model))
        self.assertNotIn("do-not-leak-this-secret", f"{model}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && poetry run pytest tests/test_repositories/test_models/test_mcp_tool.py -v`
Expected: FAIL(現在の `McpConfigModel`/`McpToolModel` は `mcpConfig`(単数)しか持たないため、`mcpServers`/`secret_arn` 関連でエラー)

- [ ] **Step 3: `McpConfigModel`/`McpToolModel` を書き換える**

`backend/app/repositories/models/custom_bot.py` の現在の(257〜331行目相当)

```python
class McpConfigModel(BaseModel):
    endpoint_url: str
    client_id: str
    secret_arn: str
    client_secret: SecureString = Field(..., repr=False)

    @classmethod
    def from_mcp_config(cls, config: McpConfig, user_id: str, bot_id: str) -> Self:
        """Create a configuration model from the input and save the client secret to Secrets Manager"""
        secret_arn = store_api_key_to_secret_manager(
            user_id, bot_id, "mcp", config.client_secret
        )

        return cls(
            endpoint_url=config.endpoint_url,
            client_id=config.client_id,
            secret_arn=secret_arn,
            client_secret=config.client_secret,
        )

    @model_validator(mode="before")
    @classmethod
    def load_secret_from_arn(cls, data):
        """Load the client secret from Secrets Manager when it is empty"""
        if (
            isinstance(data, dict)
            and "client_secret" in data
            and data["client_secret"] == ""
            and "secret_arn" in data
        ):
            try:
                client_secret = get_api_key_from_secret_manager(data["secret_arn"])
                data["client_secret"] = client_secret
            except Exception as e:
                logger.error(f"Failed to retrieve MCP secret from ARN: {e}")
                raise ValueError(
                    f"Failed to retrieve MCP secret from ARN: {data['secret_arn']}"
                )

        return data


class McpToolModel(BaseModel):
    tool_type: Literal["mcp"] = Field(
        "mcp",
        description="Type of tool. It does need additional settings for the MCP server connection.",
    )
    name: str
    description: str
    mcpConfig: Optional[McpConfigModel] | None = None

    @model_validator(mode="before")
    @classmethod
    def load_mcp_secret(cls, data):
        """Ensures validation of nested `McpConfigModel` with secret loading."""
        if (
            isinstance(data, dict)
            and data.get("mcpConfig")
            and isinstance(data["mcpConfig"], dict)
        ):
            data["mcpConfig"] = McpConfigModel.model_validate(data["mcpConfig"])
        return data

    @classmethod
    def from_tool_input(cls, tool: McpTool, user_id: str, bot_id: str) -> Self:
        mcp_config = None
        if tool.mcpConfig:
            mcp_config = McpConfigModel.from_mcp_config(tool.mcpConfig, user_id, bot_id)

        return cls(
            tool_type="mcp",
            name=tool.name,
            description=tool.description,
            mcpConfig=mcp_config,
        )
```

を以下に置き換える:

```python
class McpConfigModel(BaseModel):
    label: str
    endpoint_url: str
    client_id: str
    client_secret: SecureString = Field(..., repr=False)

    @classmethod
    def from_mcp_config(cls, config: McpConfig) -> Self:
        """Create a configuration model from the input (secret storage is handled once,
        for all servers together, at the McpToolModel level)."""
        return cls(
            label=config.label,
            endpoint_url=config.endpoint_url,
            client_id=config.client_id,
            client_secret=config.client_secret,
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
        """Load per-server client secrets from the shared Secrets Manager entry when empty.

        All of a bot's MCP server secrets are stored together as one JSON blob
        (`{label: client_secret, ...}`) under a single Secrets Manager entry
        (`secret_arn`), keyed by each server's `label`.
        """
        if isinstance(data, dict) and data.get("mcpServers") and data.get("secret_arn"):
            servers = data["mcpServers"]
            needs_load = any(
                isinstance(s, dict) and s.get("client_secret", "") == "" for s in servers
            )
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
                    if isinstance(s, dict) and s.get("client_secret", "") == "":
                        s["client_secret"] = secrets_by_label.get(s.get("label"), "")
        return data

    @classmethod
    def from_tool_input(cls, tool: McpTool, user_id: str, bot_id: str) -> Self:
        secret_arn = None
        if tool.mcpServers:
            secrets_by_label = {
                server.label: server.client_secret for server in tool.mcpServers
            }
            secret_arn = store_api_key_to_secret_manager(
                user_id, bot_id, "mcp", json.dumps(secrets_by_label)
            )

        servers = [
            McpConfigModel.from_mcp_config(server) for server in tool.mcpServers
        ]

        return cls(
            tool_type="mcp",
            name=tool.name,
            description=tool.description,
            mcpServers=servers,
            secret_arn=secret_arn,
        )
```

`ToolModel` の discriminated union定義・`AgentModel.from_agent_input`/`AgentModel.to_agent()` の呼び出し方は変更不要(`McpToolModel.from_tool_input(tool_input, user_id, bot_id)` の呼び出しシグネチャは変わらない)。

`AgentModel.to_agent()` 内の既存の

```python
            elif isinstance(tool, McpToolModel):
                tools.append(
                    McpTool(
                        tool_type="mcp",
                        name=tool.name,
                        description=tool.description,
                        mcpConfig=(
                            McpConfig(
                                endpoint_url=tool.mcpConfig.endpoint_url,
                                client_id=tool.mcpConfig.client_id,
                                client_secret=tool.mcpConfig.client_secret,
                            )
                            if tool.mcpConfig
                            else None
                        ),
                    )
                )
```

を以下に置き換える:

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
                                client_id=server.client_id,
                                client_secret=server.client_secret,
                            )
                            for server in tool.mcpServers
                        ],
                    )
                )
```

最後に、ファイル冒頭(1行目)に `import json` を追加する:

```python
import json
import logging
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_repositories/test_models/test_mcp_tool.py -v`
Expected: PASS(4 tests)

- [ ] **Step 5: 既存のbotモデルテストにデグレがないことを確認**

Run: `cd backend && poetry run pytest tests/test_repositories/test_models/test_bot.py -v`
Expected: 既存と同じ結果(3 passed、4 pre-existing errors — 実Cognito環境依存で無関係)

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/models/custom_bot.py backend/tests/test_repositories/test_models/test_mcp_tool.py
git commit -m "feat: support multiple MCP servers per bot with a shared Secrets Manager entry"
```

---

### Task 3: 複数MCP接続のライフサイクル管理とツール名の一意化

**Files:**
- Modify: `backend/app/strands_integration/tools/mcp_tools.py`
- Modify: `backend/tests/test_strands_integration/test_mcp_tools.py`(該当部分を全面書き換え)

**Interfaces:**
- Consumes: `McpToolModel`(Task 2、`mcpServers: list[McpConfigModel]`, `secret_arn: str | None`)
- Produces: `mcp_tools_scope(bot: BotModel | None)`(変更後: 複数サーバー対応、返すツールリストは各ツール名に `{label}_` prefix済み)

- [ ] **Step 1: 失敗するテストを書く(既存テストファイルの `TestMcpToolsScope` 部分を全面書き換え)**

`backend/tests/test_strands_integration/test_mcp_tools.py` の80行目(`from app.repositories.models.custom_bot import (`)以降、ファイル末尾までを以下で置き換える(`TestGetMcpBearerToken` クラスはそのまま維持):

```python
from app.repositories.models.custom_bot import (
    ActiveModelsModel,
    AgentModel,
    GenerationParamsModel,
    KnowledgeModel,
    McpConfigModel,
    McpToolModel,
    ReasoningParamsModel,
    UsageStatsModel,
)
from app.repositories.models.custom_bot import BotModel
from app.strands_integration.tools.mcp_tools import mcp_tools_scope


def _make_bot(tools):
    return BotModel(
        id="test-bot",
        title="Test Bot",
        description="",
        instruction="",
        create_time=1627984879.9,
        last_used_time=1627984879.9,
        shared_scope="private",
        shared_status="unshared",
        allowed_cognito_groups=[],
        allowed_cognito_users=[],
        is_starred=False,
        owner_user_id="test-user",
        generation_params=GenerationParamsModel(
            max_tokens=2000,
            top_k=250,
            top_p=0.999,
            temperature=0.6,
            stop_sequences=["Human: ", "Assistant: "],
            reasoning_params=ReasoningParamsModel(budget_tokens=1024),
        ),
        agent=AgentModel(tools=tools),
        knowledge=KnowledgeModel(
            source_urls=[], sitemap_urls=[], filenames=[], s3_urls=[]
        ),
        prompt_caching_enabled=False,
        sync_status="RUNNING",
        sync_status_reason="reason",
        sync_last_exec_id="",
        published_api_stack_name=None,
        published_api_datetime=None,
        published_api_codebuild_id=None,
        display_retrieved_chunks=True,
        conversation_quick_starters=[],
        bedrock_knowledge_base=None,
        bedrock_guardrails=None,
        active_models=ActiveModelsModel(),
        usage_stats=UsageStatsModel(usage_count=0),
    )


def _make_mcp_tool(*servers):
    return McpToolModel(
        tool_type="mcp",
        name="mcp",
        description="MCP knowledge search",
        secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/test-user/test-bot",
        mcpServers=list(servers),
    )


def _make_server(label, endpoint="https://example.com/mcp", client_id="client-1"):
    return McpConfigModel(
        label=label,
        endpoint_url=endpoint,
        client_id=client_id,
        client_secret="s3cr3t",
    )


class _FakeMcpTool:
    """Stand-in for strands.tools.mcp.MCPAgentTool: exposes a mutable `.mcp_tool.name`."""

    def __init__(self, name):
        self.mcp_tool = type("_Raw", (), {"name": name})()


class TestMcpToolsScope(unittest.TestCase):
    def test_yields_empty_list_when_bot_is_none(self):
        with mcp_tools_scope(None) as tools:
            self.assertEqual(tools, [])

    def test_yields_empty_list_when_no_mcp_tool_configured(self):
        bot = _make_bot([])
        with mcp_tools_scope(bot) as tools:
            self.assertEqual(tools, [])

    def test_yields_empty_list_when_mcp_tool_has_no_servers(self):
        bot = _make_bot([_make_mcp_tool()])
        with mcp_tools_scope(bot) as tools:
            self.assertEqual(tools, [])

    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_yields_empty_list_on_connection_failure(self, mock_get_token):
        mock_get_token.side_effect = Exception("token fetch failed")
        bot = _make_bot([_make_mcp_tool(_make_server("powersort"))])

        with mcp_tools_scope(bot) as tools:
            self.assertEqual(tools, [])

    @patch("app.strands_integration.tools.mcp_tools.MCPClient")
    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_prefixes_tool_names_with_server_label(
        self, mock_get_token, mock_mcp_client_cls
    ):
        mock_get_token.return_value = "token-1"
        mock_client_instance = MagicMock()
        mock_client_instance.list_tools_sync.return_value = [_FakeMcpTool("search")]
        mock_mcp_client_cls.return_value = mock_client_instance

        bot = _make_bot([_make_mcp_tool(_make_server("powersort"))])

        with mcp_tools_scope(bot) as tools:
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0].mcp_tool.name, "powersort_search")

    @patch("app.strands_integration.tools.mcp_tools.MCPClient")
    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_one_server_failure_does_not_block_the_others(
        self, mock_get_token, mock_mcp_client_cls
    ):
        def token_side_effect(client_id, client_secret, domain, cache_key):
            if client_id == "bad-client":
                raise Exception("token fetch failed")
            return "token-1"

        mock_get_token.side_effect = token_side_effect

        good_client = MagicMock()
        good_client.list_tools_sync.return_value = [_FakeMcpTool("search")]

        def client_factory(*args, **kwargs):
            return good_client

        mock_mcp_client_cls.side_effect = client_factory

        bot = _make_bot(
            [
                _make_mcp_tool(
                    _make_server("badserver", client_id="bad-client"),
                    _make_server("goodserver", client_id="good-client"),
                )
            ]
        )

        with mcp_tools_scope(bot) as tools:
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0].mcp_tool.name, "goodserver_search")

    @patch("app.strands_integration.tools.mcp_tools.MCPClient")
    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_body_exception_propagates_unchanged(
        self, mock_get_token, mock_mcp_client_cls
    ):
        mock_get_token.return_value = "token-1"
        mock_client_instance = MagicMock()
        mock_client_instance.list_tools_sync.return_value = []
        mock_mcp_client_cls.return_value = mock_client_instance

        bot = _make_bot([_make_mcp_tool(_make_server("powersort"))])

        class BodyError(Exception):
            pass

        with self.assertRaises(BodyError):
            with mcp_tools_scope(bot) as tools:
                raise BodyError("boom")

    @patch("app.strands_integration.tools.mcp_tools.MCPClient")
    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_teardown_failure_does_not_propagate(
        self, mock_get_token, mock_mcp_client_cls
    ):
        mock_get_token.return_value = "token-1"
        mock_client_instance = MagicMock()
        mock_client_instance.list_tools_sync.return_value = []
        mock_client_instance.__exit__.side_effect = Exception("teardown failed")
        mock_mcp_client_cls.return_value = mock_client_instance

        bot = _make_bot([_make_mcp_tool(_make_server("powersort"))])

        with mcp_tools_scope(bot) as tools:
            self.assertEqual(tools, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && poetry run pytest tests/test_strands_integration/test_mcp_tools.py -v`
Expected: FAIL(現在の `_get_mcp_tool_config`/`mcp_tools_scope` は単一 `mcpConfig` 前提のため、`mcpServers`/複数サーバー関連のテストが失敗)

- [ ] **Step 3: `mcp_tools.py` を複数サーバー対応に書き換える**

`backend/app/strands_integration/tools/mcp_tools.py` の冒頭importを以下に置き換える:

```python
"""
MCP server integration - token acquisition/caching and tool scoping.
"""

import logging
import os
import time
from contextlib import ExitStack, contextmanager

import requests
from app.repositories.models.custom_bot import BotModel, McpToolModel
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient
```

`get_mcp_bearer_token` 関数はそのまま変更しない。

`_get_mcp_tool_config` と `mcp_tools_scope` の現在の実装

```python
def _get_mcp_tool_config(bot: BotModel | None):
    """Extract MCP tool configuration from bot."""
    if not bot or not bot.agent or not bot.agent.tools:
        return None

    for tool_config in bot.agent.tools:
        if tool_config.tool_type == "mcp" and tool_config.mcpConfig:
            return tool_config.mcpConfig

    return None


@contextmanager
def mcp_tools_scope(bot: BotModel | None):
    """Open an MCP connection scoped to a single chat turn and yield its tools.

    Yields an empty list when the bot has no MCP tool configured, or when
    the connection/token fetch fails (degrade gracefully, keep the chat working).
    """
    config = _get_mcp_tool_config(bot)
    if not config:
        yield []
        return

    try:
        token = get_mcp_bearer_token(
            config.client_id,
            config.client_secret,
            COGNITO_MCP_AUTH_DOMAIN,
            config.secret_arn,
        )
        client = MCPClient(
            lambda: streamablehttp_client(
                config.endpoint_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=MCP_CONNECTION_TIMEOUT_SECONDS,
            ),
            startup_timeout=MCP_CONNECTION_TIMEOUT_SECONDS,
        )
        client.__enter__()
    except Exception as e:
        logger.error(f"MCP connection failed, falling back without MCP tools: {e}")
        yield []
        return

    try:
        tools = client.list_tools_sync()
    except Exception as e:
        logger.error(f"MCP connection failed, falling back without MCP tools: {e}")
        try:
            client.__exit__(None, None, None)  # type: ignore[arg-type]
        except Exception as close_error:
            logger.error(f"Error closing MCP client: {close_error}")
        yield []
        return

    try:
        yield tools
    finally:
        try:
            client.__exit__(None, None, None)  # type: ignore[arg-type]
        except Exception as close_error:
            logger.error(f"Error closing MCP client: {close_error}")
```

を以下に置き換える:

```python
def _get_mcp_tool(bot: BotModel | None) -> McpToolModel | None:
    """Extract the bot's MCP tool configuration (all configured servers)."""
    if not bot or not bot.agent or not bot.agent.tools:
        return None

    for tool_config in bot.agent.tools:
        if tool_config.tool_type == "mcp":
            return tool_config

    return None


def _safe_close(client: MCPClient, label: str) -> None:
    try:
        client.__exit__(None, None, None)  # type: ignore[arg-type]
    except Exception as close_error:
        logger.error(f"Error closing MCP client for server '{label}': {close_error}")


@contextmanager
def mcp_tools_scope(bot: BotModel | None):
    """Open one MCP connection per configured server, scoped to a single chat turn,
    and yield the combined list of tools with names prefixed by each server's label.

    Each server is independent: a connection/token/list_tools failure for one
    server only drops that server's tools (logged), other servers' tools and
    the chat continue normally.
    """
    mcp_tool = _get_mcp_tool(bot)
    if not mcp_tool or not mcp_tool.mcpServers:
        yield []
        return

    combined_tools = []
    with ExitStack() as stack:
        for server in mcp_tool.mcpServers:
            try:
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
            except Exception as e:
                logger.error(
                    f"MCP server '{server.label}' connection failed, "
                    f"skipping its tools: {e}"
                )
                continue

            stack.callback(_safe_close, client, server.label)

            try:
                server_tools = client.list_tools_sync()
            except Exception as e:
                logger.error(
                    f"MCP server '{server.label}' list_tools failed, "
                    f"skipping its tools: {e}"
                )
                continue

            for tool in server_tools:
                tool.mcp_tool.name = f"{server.label}_{tool.mcp_tool.name}"
            combined_tools.extend(server_tools)

        yield combined_tools
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_strands_integration/test_mcp_tools.py -v`
Expected: PASS(全テスト)

- [ ] **Step 5: mypy確認**

Run: `cd backend && poetry run mypy --config-file mypy.ini app/strands_integration/tools/mcp_tools.py`
Expected: `Success: no issues found`(既存の `# type: ignore[arg-type]` は `_safe_close` 内に引き継がれているため問題ないはずだが、`stack.callback` 経由の呼び出しで型エラーが出た場合は同様に `# type: ignore` を追加して解消する)

- [ ] **Step 6: Commit**

```bash
git add backend/app/strands_integration/tools/mcp_tools.py backend/tests/test_strands_integration/test_mcp_tools.py
git commit -m "feat: connect to multiple MCP servers per bot with per-server degradation and tool-name prefixing"
```

---

### Task 4: フロントエンド型定義の複数サーバー対応

**Files:**
- Modify: `frontend/src/features/agent/types/index.d.ts`

**Interfaces:**
- Produces: `McpConfig`(`label: string` 追加)、`McpAgentTool`(`mcpServers: McpConfig[]`、旧 `mcpConfig?` を置き換え)

- [ ] **Step 1: 型定義を変更する**

`frontend/src/features/agent/types/index.d.ts` の現在の

```typescript
export type McpConfig = {
  endpointUrl: string;
  clientId: string;
  clientSecret: string;
};

export type McpAgentTool = {
  toolType: 'mcp';
  name: string;
  description: string;
  mcpConfig?: McpConfig;
};
```

を以下に置き換える:

```typescript
export type McpConfig = {
  label: string;
  endpointUrl: string;
  clientId: string;
  clientSecret: string;
};

export type McpAgentTool = {
  toolType: 'mcp';
  name: string;
  description: string;
  mcpServers: McpConfig[];
};
```

- [ ] **Step 2: TypeScript型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラーが出る(この時点では `McpConfig.tsx`/`AvailableTools.tsx`/`BotKbEditPage.tsx` がまだ古い `mcpConfig` 単数形を参照しているため。Task 5〜8で解消される想定。ここでは型定義自体が意図通りに書けているかの確認のみ行い、以降のタスクで解消されることを前提に進める)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/agent/types/index.d.ts
git commit -m "feat: change McpAgentTool to support multiple MCP servers"
```

---

### Task 5: MCP設定入力コンポーネントのラベル対応・i18n追加

**Files:**
- Modify: `frontend/src/features/agent/components/McpConfig.tsx`
- Modify: `frontend/src/i18n/ja/index.ts`
- Modify: `frontend/src/i18n/en/index.ts`
- Modify: `frontend/src/i18n/pt-br/index.ts`

**Interfaces:**
- Consumes: `McpConfig`型(Task 4)
- Produces: `label` 入力欄を追加した `McpConfig` コンポーネント、i18nキー `agent.tools.mcpConfig.label.{label,placeholder}`、`agent.tools.mcp.{addServer,removeServer}`

- [ ] **Step 1: `McpConfig.tsx` に `label` 入力欄を追加する**

`frontend/src/features/agent/components/McpConfig.tsx` の現在の

```typescript
  return (
    <div className="space-y-4">
      <InputText
        label={t('agent.tools.mcpConfig.endpointUrl.label')}
        placeholder={t('agent.tools.mcpConfig.endpointUrl.placeholder')}
        value={config.endpointUrl}
        onChange={(value) => onChange({ ...config, endpointUrl: value })}
      />
```

を以下に置き換える:

```typescript
  return (
    <div className="space-y-4">
      <InputText
        label={t('agent.tools.mcpConfig.label.label')}
        placeholder={t('agent.tools.mcpConfig.label.placeholder')}
        value={config.label}
        onChange={(value) => onChange({ ...config, label: value })}
      />
      <InputText
        label={t('agent.tools.mcpConfig.endpointUrl.label')}
        placeholder={t('agent.tools.mcpConfig.endpointUrl.placeholder')}
        value={config.endpointUrl}
        onChange={(value) => onChange({ ...config, endpointUrl: value })}
      />
```

- [ ] **Step 2: 日本語i18nキーを追加する**

`frontend/src/i18n/ja/index.ts` の現在の

```typescript
        mcp: {
          name: 'MCPサーバー',
          description: '設定されたMCPサーバーに接続してツールを利用します。',
        },
        mcpConfig: {
          endpointUrl: {
```

を以下に置き換える:

```typescript
        mcp: {
          name: 'MCPサーバー',
          description: '設定されたMCPサーバーに接続してツールを利用します。',
          addServer: 'サーバーを追加',
          removeServer: '削除',
        },
        mcpConfig: {
          label: {
            label: 'サーバーラベル',
            placeholder: '英数字とアンダースコアのみ(例: powersort)',
          },
          endpointUrl: {
```

- [ ] **Step 3: 英語i18nキーを追加する**

`frontend/src/i18n/en/index.ts` の現在の

```typescript
        mcp: {
          name: 'MCP Server',
          description: 'Connect to the configured MCP server to use its tools.',
        },
        mcpConfig: {
          endpointUrl: {
```

を以下に置き換える:

```typescript
        mcp: {
          name: 'MCP Server',
          description: 'Connect to the configured MCP server to use its tools.',
          addServer: 'Add Server',
          removeServer: 'Remove',
        },
        mcpConfig: {
          label: {
            label: 'Server Label',
            placeholder: 'Letters, digits, underscores only (e.g. powersort)',
          },
          endpointUrl: {
```

- [ ] **Step 4: ポルトガル語i18nキーを追加する**

`frontend/src/i18n/pt-br/index.ts` の現在の

```typescript
        mcp: {
          name: 'Servidor MCP',
          description: 'Conecte-se ao servidor MCP configurado para usar suas ferramentas.',
        },
        mcpConfig: {
          endpointUrl: {
```

を以下に置き換える:

```typescript
        mcp: {
          name: 'Servidor MCP',
          description: 'Conecte-se ao servidor MCP configurado para usar suas ferramentas.',
          addServer: 'Adicionar Servidor',
          removeServer: 'Remover',
        },
        mcpConfig: {
          label: {
            label: 'Rótulo do Servidor',
            placeholder: 'Apenas letras, números e sublinhado (ex: powersort)',
          },
          endpointUrl: {
```

- [ ] **Step 5: TypeScript型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: `McpConfig.tsx` 自体のエラーは解消。`AvailableTools.tsx`/`BotKbEditPage.tsx` 側のエラーは残る(Task 7・8で解消)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/agent/components/McpConfig.tsx frontend/src/i18n/ja/index.ts frontend/src/i18n/en/index.ts frontend/src/i18n/pt-br/index.ts
git commit -m "feat: add server label field to MCP config input and i18n strings"
```

---

### Task 6: MCPサーバーリスト管理コンポーネント新規作成

**Files:**
- Create: `frontend/src/features/agent/components/McpServersConfig.tsx`

**Interfaces:**
- Consumes: `McpConfig`型・`McpConfig`コンポーネント(Task 4, 5)
- Produces: `McpServersConfig`コンポーネント(`{ servers: McpConfigType[]; onChange: (servers: McpConfigType[]) => void }`)

- [ ] **Step 1: `McpServersConfig.tsx` を新規作成する**

```typescript
import { useTranslation } from 'react-i18next';
import { McpConfig as McpConfigType } from '../types';
import { McpConfig as McpConfigComponent } from './McpConfig';
import Button from '../../../components/Button';

type Props = {
  servers: McpConfigType[];
  onChange: (servers: McpConfigType[]) => void;
};

const EMPTY_SERVER: McpConfigType = {
  label: '',
  endpointUrl: '',
  clientId: '',
  clientSecret: '',
};

export const McpServersConfig = ({ servers, onChange }: Props) => {
  const { t } = useTranslation();

  const handleServerChange = (index: number, config: McpConfigType) => {
    onChange(servers.map((server, i) => (i === index ? config : server)));
  };

  const handleRemoveServer = (index: number) => {
    onChange(servers.filter((_, i) => i !== index));
  };

  const handleAddServer = () => {
    onChange([...servers, { ...EMPTY_SERVER }]);
  };

  return (
    <div className="space-y-4">
      {servers.map((server, index) => (
        <div
          key={index}
          className="flex items-start gap-2 border-b border-aws-font-color-gray/30 pb-4">
          <div className="flex-1">
            <McpConfigComponent
              config={server}
              onChange={(config) => handleServerChange(index, config)}
            />
          </div>
          <Button
            outlined
            className="mt-1"
            onClick={() => handleRemoveServer(index)}>
            {t('agent.tools.mcp.removeServer')}
          </Button>
        </div>
      ))}
      <Button outlined onClick={handleAddServer}>
        {t('agent.tools.mcp.addServer')}
      </Button>
    </div>
  );
};
```

上記の `Button` の使い方(デフォルトexport、`outlined`/`className`/`onClick`/`children` プロパティ)は `frontend/src/components/Button.tsx` の実装と一致することを確認済み。

- [ ] **Step 2: TypeScript型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: `McpServersConfig.tsx` 自体にエラーがないこと(未使用であることによる警告は出ない想定。`Button` の実プロパティと合っているか確認)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/agent/components/McpServersConfig.tsx
git commit -m "feat: add MCP servers list management component (add/remove)"
```

---

### Task 7: bot作成/編集画面へのリストUI配線

**Files:**
- Modify: `frontend/src/features/agent/components/AvailableTools.tsx`

**Interfaces:**
- Consumes: `McpServersConfig`(Task 6)

- [ ] **Step 1: importを更新する**

`frontend/src/features/agent/components/AvailableTools.tsx` の

```typescript
import { McpConfig as McpConfigComponent } from './McpConfig';
```

を以下に置き換える:

```typescript
import { McpServersConfig } from './McpServersConfig';
```

(`McpConfig as McpConfigType` のimportはそのまま維持。`McpConfig.tsx` の直接importが不要になった場合はESLintの未使用import警告に従って削除する)

- [ ] **Step 2: `handleChangeTool` の `mcp` 分岐を更新する**

現在の

```typescript
      } else if (tool.name === 'mcp') {
        setTools((preTools) => {
          const isEnabled = preTools
            ?.map(({ name }) => name)
            .includes(tool.name);

          const newTools = isEnabled
            ? [...preTools.filter(({ name }) => name != tool.name)]
            : [
                ...preTools,
                {
                  ...tool,
                  toolType: 'mcp' as ToolType,
                  name: 'mcp',
                  mcpConfig: {
                    endpointUrl: '',
                    clientId: '',
                    clientSecret: '',
                  },
                } as AgentTool,
              ];

          return newTools;
        });
      } else {
```

を以下に置き換える:

```typescript
      } else if (tool.name === 'mcp') {
        setTools((preTools) => {
          const isEnabled = preTools
            ?.map(({ name }) => name)
            .includes(tool.name);

          const newTools = isEnabled
            ? [...preTools.filter(({ name }) => name != tool.name)]
            : [
                ...preTools,
                {
                  ...tool,
                  toolType: 'mcp' as ToolType,
                  name: 'mcp',
                  mcpServers: [],
                } as AgentTool,
              ];

          return newTools;
        });
      } else {
```

- [ ] **Step 3: `handleMcpConfigChange` を `handleMcpServersChange` に置き換える**

現在の

```typescript
  const handleMcpConfigChange = useCallback(
    (config: McpConfigType) => {
      setTools((prevTools) =>
        prevTools.map((tool) => {
          if (tool.name === 'mcp') {
            return {
              ...tool,
              toolType: 'mcp' as ToolType,
              name: 'mcp',
              mcpConfig: config,
            } as AgentTool;
          }
          return tool;
        })
      );
    },
    [setTools]
  );
```

を以下に置き換える:

```typescript
  const handleMcpServersChange = useCallback(
    (servers: McpConfigType[]) => {
      setTools((prevTools) =>
        prevTools.map((tool) => {
          if (tool.name === 'mcp') {
            return {
              ...tool,
              toolType: 'mcp' as ToolType,
              name: 'mcp',
              mcpServers: servers,
            } as AgentTool;
          }
          return tool;
        })
      );
    },
    [setTools]
  );
```

- [ ] **Step 4: 描画ブロックを更新する**

現在の

```typescript
          {tool.name === 'mcp' &&
            tools?.map(({ name }) => name).includes('mcp') && (
              <div className="space-y-4">
                <div className="ml-6 text-sm">
                  <McpConfigComponent
                    config={
                      tools.find(
                        (t): t is McpAgentTool => t.name === 'mcp' && isMcpTool(t)
                      )?.mcpConfig || {
                        endpointUrl: '',
                        clientId: '',
                        clientSecret: '',
                      }
                    }
                    onChange={handleMcpConfigChange}
                  />
                </div>
              </div>
            )}
```

を以下に置き換える:

```typescript
          {tool.name === 'mcp' &&
            tools?.map(({ name }) => name).includes('mcp') && (
              <div className="space-y-4">
                <div className="ml-6 text-sm">
                  <McpServersConfig
                    servers={
                      tools.find(
                        (t): t is McpAgentTool => t.name === 'mcp' && isMcpTool(t)
                      )?.mcpServers || []
                    }
                    onChange={handleMcpServersChange}
                  />
                </div>
              </div>
            )}
```

- [ ] **Step 5: TypeScript型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: `AvailableTools.tsx` 起因のエラーは解消(`BotKbEditPage.tsx` 側のエラーはTask 8で解消)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/agent/components/AvailableTools.tsx
git commit -m "feat: wire MCP servers list UI into the bot creation/edit screen"
```

---

### Task 8: 保存前バリデーションの複数サーバー対応

**Files:**
- Modify: `frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx`

**Interfaces:**
- Consumes: `McpAgentTool.mcpServers`(Task 4, 7)

- [ ] **Step 1: `isToolValid` のmcp検証部分を更新する**

`frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx` の現在の

```typescript
      // Mcp tool validation
      if (isMcpTool(tool) && !tool.mcpConfig?.endpointUrl) {
        setErrorMessages(
          `tools-${idx}-mcpConfig.endpoint_url`,
          t('input.validationError.required')
        );
        return true;
      }

      if (isMcpTool(tool) && !tool.mcpConfig?.clientId) {
        setErrorMessages(
          `tools-${idx}-mcpConfig.client_id`,
          t('input.validationError.required')
        );
        return true;
      }

      if (isMcpTool(tool) && !tool.mcpConfig?.clientSecret) {
        setErrorMessages(
          `tools-${idx}-mcpConfig.client_secret`,
          t('input.validationError.required')
        );
        return true;
      }
```

を以下に置き換える:

```typescript
      // Mcp tool validation: every configured server must have all fields filled,
      // and labels must be unique within the bot.
      if (isMcpTool(tool)) {
        const labels = tool.mcpServers.map((server) => server.label);
        const hasDuplicateLabel = labels.some(
          (label, labelIdx) => label !== '' && labels.indexOf(label) !== labelIdx
        );

        if (hasDuplicateLabel) {
          setErrorMessages(
            `tools-${idx}-mcpServers.label`,
            t('input.validationError.required')
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
            t('input.validationError.required')
          );
          return true;
        }
      }
```

(既存の `setErrorMessages`/`t('input.validationError.required')` の呼び出し規約をそのまま踏襲。エラーメッセージが実際にUI上どこにも表示されない、というのは既存の `bedrock_agent` 検証にもある既知の制約で、この変更のスコープ外)

- [ ] **Step 2: TypeScript型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラーなし(Task 4〜8を通してプロジェクト全体でエラーが解消していること)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx
git commit -m "feat: validate all configured MCP servers before bot save"
```

---

## 実装後の手動確認

1. bot作成画面でMCPツールを有効化し、「サーバーを追加」で2つ以上のMCPサーバー(異なるKB)を登録してbotを保存する
2. そのbotとチャットし、両方のKBに対応する質問を投げて、それぞれのサーバー由来のツールが(labelプレフィックス付きの名前で)呼び出されることをログで確認する
3. 片方のサーバーのClient Secretを意図的に誤らせてチャットし、もう片方のサーバーのツールは使えたまま、チャット自体が継続することを確認する(サーバー単位の縮退運転の確認)
4. bot編集画面を開き直し、既存の複数サーバー設定(ラベル・エンドポイント・Client ID・Client Secretの実際の値)が正しく復元されることを確認する

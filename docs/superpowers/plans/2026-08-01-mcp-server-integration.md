# 固定MCPサーバー連携 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** botの新しいツールタイプ `mcp` を追加し、bot作成者が指定したMCPサーバー(Cognito `client_credentials` grant認証)にチャットターン単位で接続し、そのMCPサーバーが提供するツールをStrands Agentから利用できるようにする。

**Architecture:** 既存の `bedrock_agent` / `internet` ツールと同じパターンで `bot.agent.tools` に `McpToolModel`(endpoint URL・Cognito Client ID・Client Secret)を追加する。Client SecretはFirecrawl API keyと同じ規約でAWS Secrets Managerに保存する。チャット1ターンごとに `mcp_tools_scope()` コンテキストマネージャがCognitoからBearerトークンを取得(Lambdaコンテナ内メモリキャッシュ)し、`strands.tools.mcp.MCPClient` を開いて `list_tools_sync()` でツール一覧を取得、Strands Agentの `extra_tools` として渡す。接続失敗時はログのみ出力し空リストにフォールバックする(縮退運転)。

**Tech Stack:** Python(FastAPI backend, strands-agents SDK, boto3, requests), TypeScript/React(frontend), pytest, Secrets Manager

## Global Constraints

- bot 1つにつきMCP接続は1つ(1 bot = 1 KB)。複数KB同時接続はスコープ外
- MCP接続はチャット1ターンの間だけopen/closeする(常時接続にしない)
- Cognitoトークンエンドポイント: `https://knowledge-mcp-auth.auth.<region>.amazoncognito.com/oauth2/token`(regionは `BEDROCK_REGION` から組み立て)
- `scope=knowledge-mcp/invoke` は固定値
- アクセストークンはLambdaコンテナ内メモリキャッシュ。有効期限の90秒前に再取得する
- MCP接続・トークン取得の失敗はログ出力のみで、チャット自体は継続する(既存のFirecrawl失敗時フォールバックと同じ考え方)
- Client Secretの保存・復元は既存の `store_api_key_to_secret_manager` / `get_api_key_from_secret_manager`(`backend/app/utils.py`)をそのまま再利用する。マスキングはせず、既存のFirecrawl API keyと同じくbot所有者が編集画面を開いた際は実際の値を返す
- 参照設計書: `docs/superpowers/specs/2026-08-01-mcp-server-integration-design.md`

---

### Task 1: MCPツールのAPIスキーマ追加(routes層)

**Files:**
- Modify: `backend/app/routes/schemas/bot.py`

**Interfaces:**
- Produces: `McpConfig`(BaseSchema: `endpoint_url: str`, `client_id: str`, `client_secret: str`)、`McpTool`(BaseSchema: `tool_type: Literal["mcp"]`, `name: str`, `description: str`, `mcpConfig: Optional[McpConfig] | None`)、更新後の `Tool` union

- [ ] **Step 1: `McpConfig` と `McpTool` を追加し `Tool` unionに組み込む**

`backend/app/routes/schemas/bot.py` の `BedrockAgentTool` クラス定義の直後(139行目の `Tool = Annotated[...]` の手前)に追加:

```python
class McpConfig(BaseSchema):
    endpoint_url: str
    client_id: str
    client_secret: str

    @field_validator("endpoint_url")
    def validate_endpoint_url(cls, v):
        if v == "":
            raise ValueError("MCP endpoint URL is empty")
        return v

    @field_validator("client_id")
    def validate_client_id(cls, v):
        if v == "":
            raise ValueError("MCP client ID is empty")
        return v


class McpTool(BaseSchema):
    tool_type: Literal["mcp"] = "mcp"
    name: str
    description: str
    mcpConfig: Optional[McpConfig] | None = None
```

そして既存の

```python
Tool = Annotated[
    PlainTool | InternetTool | BedrockAgentTool, Discriminator("tool_type")
]
```

を以下に置き換える:

```python
Tool = Annotated[
    PlainTool | InternetTool | BedrockAgentTool | McpTool, Discriminator("tool_type")
]
```

- [ ] **Step 2: Pythonの構文チェック**

Run: `cd backend && python -c "from app.routes.schemas.bot import McpConfig, McpTool, Tool; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/routes/schemas/bot.py
git commit -m "feat: add MCP tool API schema"
```

---

### Task 2: MCPツールのリポジトリモデル追加とAgentModel配線

**Files:**
- Modify: `backend/app/repositories/models/custom_bot.py`
- Test: `backend/tests/test_repositories/test_models/test_mcp_tool.py`

**Interfaces:**
- Consumes: `McpConfig`, `McpTool`(Task 1), `store_api_key_to_secret_manager`/`get_api_key_from_secret_manager`(既存 `backend/app/utils.py`)
- Produces: `McpConfigModel`(`endpoint_url: str`, `client_id: str`, `secret_arn: str`, `client_secret: SecureString`)、`McpToolModel`(`tool_type: Literal["mcp"]`, `name: str`, `description: str`, `mcpConfig: Optional[McpConfigModel] | None`)、更新後の `ToolModel` union、`AgentModel.from_agent_input`/`AgentModel.to_agent()` の `mcp` 対応

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_repositories/test_models/test_mcp_tool.py` を新規作成:

```python
import sys

sys.path.append(".")
import unittest
from unittest.mock import patch

from app.repositories.models.custom_bot import AgentModel, McpConfigModel, McpToolModel
from app.routes.schemas.bot import AgentInput, McpConfig, McpTool


class TestMcpToolModel(unittest.TestCase):
    @patch("app.repositories.models.custom_bot.store_api_key_to_secret_manager")
    def test_from_agent_input_stores_secret(self, mock_store):
        mock_store.return_value = "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1"

        agent_input = AgentInput(
            tools=[
                McpTool(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpConfig=McpConfig(
                        endpoint_url="https://example.com/mcp",
                        client_id="client-1",
                        client_secret="s3cr3t",
                    ),
                )
            ]
        )

        agent_model = AgentModel.from_agent_input(agent_input, "user1", "bot1")

        self.assertEqual(len(agent_model.tools), 1)
        tool = agent_model.tools[0]
        self.assertIsInstance(tool, McpToolModel)
        self.assertEqual(tool.mcpConfig.endpoint_url, "https://example.com/mcp")
        self.assertEqual(tool.mcpConfig.client_id, "client-1")
        self.assertEqual(tool.mcpConfig.client_secret, "s3cr3t")
        self.assertEqual(
            tool.mcpConfig.secret_arn,
            "arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
        )
        mock_store.assert_called_once_with("user1", "bot1", "mcp", "s3cr3t")

    @patch("app.repositories.models.custom_bot.get_api_key_from_secret_manager")
    def test_to_agent_round_trips_config(self, mock_get_secret):
        mock_get_secret.return_value = "s3cr3t"

        agent_model = AgentModel(
            tools=[
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpConfig=McpConfigModel(
                        endpoint_url="https://example.com/mcp",
                        client_id="client-1",
                        secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/user1/bot1",
                        client_secret="s3cr3t",
                    ),
                )
            ]
        )

        agent = agent_model.to_agent()

        self.assertEqual(len(agent.tools), 1)
        tool = agent.tools[0]
        self.assertEqual(tool.tool_type, "mcp")
        self.assertEqual(tool.mcpConfig.endpoint_url, "https://example.com/mcp")
        self.assertEqual(tool.mcpConfig.client_secret, "s3cr3t")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && python -m pytest tests/test_repositories/test_models/test_mcp_tool.py -v`
Expected: FAIL(`ImportError: cannot import name 'McpConfigModel'` など)

- [ ] **Step 3: `McpConfigModel` と `McpToolModel` を実装する**

`backend/app/repositories/models/custom_bot.py` の `BedrockAgentToolModel` クラス定義の直後(255行目の `ToolModel = Annotated[...]` の手前)に追加:

```python
class McpConfigModel(BaseModel):
    endpoint_url: str
    client_id: str
    secret_arn: str
    client_secret: SecureString

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

`ToolModel` の union定義を以下に置き換える:

```python
ToolModel = Annotated[
    PlainToolModel | InternetToolModel | BedrockAgentToolModel | McpToolModel,
    Discriminator("tool_type"),
]
```

`AgentModel.from_agent_input` のループに分岐を追加(294行目の `bedrock_agent` 分岐の直後):

```python
            elif tool_input.tool_type == "bedrock_agent":
                tools.append(BedrockAgentToolModel.from_tool_input(tool_input))
            elif tool_input.tool_type == "mcp":
                tools.append(
                    McpToolModel.from_tool_input(tool_input, user_id, bot_id)
                )
```

`AgentModel.to_agent()` の分岐に追加(323行目の `BedrockAgentToolModel` 分岐の直後、`else:` の手前):

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

最後に、ファイル冒頭の `from app.routes.schemas.bot import (...)` に `McpConfig` と `McpTool` を追加する(21行目 `FirecrawlConfig,` の直後にアルファベット順で挿入):

```python
    FirecrawlConfig,
    GenerationParams,
    InternetTool,
    Knowledge,
    McpConfig,
    McpTool,
    PlainTool,
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && python -m pytest tests/test_repositories/test_models/test_mcp_tool.py -v`
Expected: PASS(2 tests)

- [ ] **Step 5: 既存のbotモデルテストにデグレがないことを確認**

Run: `cd backend && python -m pytest tests/test_repositories/test_models/test_bot.py -v`
Expected: PASS(all existing tests still pass)

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/models/custom_bot.py backend/tests/test_repositories/test_models/test_mcp_tool.py
git commit -m "feat: add MCP tool repository model with Secrets Manager-backed client secret"
```

---

### Task 3: bot削除時のMCPシークレット削除

**Files:**
- Modify: `backend/app/bot_remove.py`

**Interfaces:**
- Consumes: `delete_api_key_from_secret_manager(user_id: str, bot_id: str, prefix: str) -> None`(既存 `backend/app/utils.py:281`)

- [ ] **Step 1: `mcp` prefixでの削除呼び出しを追加**

`backend/app/bot_remove.py` の79行目:

```python
    delete_api_key_from_secret_manager(user_id, bot_id, "firecrawl")
```

を以下に置き換える:

```python
    delete_api_key_from_secret_manager(user_id, bot_id, "firecrawl")
    delete_api_key_from_secret_manager(user_id, bot_id, "mcp")
```

(`delete_api_key_from_secret_manager` はシークレットが存在しない場合 `ResourceNotFoundException` を無視して正常終了するため、MCP未設定のbotでも安全に呼び出せる)

- [ ] **Step 2: 構文チェック**

Run: `cd backend && python -c "import app.bot_remove; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/bot_remove.py
git commit -m "feat: delete MCP client secret from Secrets Manager on bot removal"
```

---

### Task 4: Cognitoアクセストークン取得・キャッシュ

**Files:**
- Create: `backend/app/strands_integration/tools/mcp_tools.py`
- Test: `backend/tests/test_strands_integration/test_mcp_tools.py`

**Interfaces:**
- Produces: `get_mcp_bearer_token(client_id: str, client_secret: str, cognito_domain: str, cache_key: str) -> str`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_strands_integration/test_mcp_tools.py` を新規作成:

```python
import sys

sys.path.append(".")
import time
import unittest
from unittest.mock import MagicMock, patch

from app.strands_integration.tools.mcp_tools import _token_cache, get_mcp_bearer_token


class TestGetMcpBearerToken(unittest.TestCase):
    def setUp(self):
        _token_cache.clear()

    @patch("app.strands_integration.tools.mcp_tools.requests.post")
    def test_fetches_and_caches_token(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "token-1",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        token = get_mcp_bearer_token("client-1", "secret-1", "example.auth.region.amazoncognito.com", "cache-key-1")

        self.assertEqual(token, "token-1")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(
            args[0],
            "https://example.auth.region.amazoncognito.com/oauth2/token",
        )
        self.assertEqual(kwargs["auth"], ("client-1", "secret-1"))
        self.assertEqual(
            kwargs["data"],
            {"grant_type": "client_credentials", "scope": "knowledge-mcp/invoke"},
        )

    @patch("app.strands_integration.tools.mcp_tools.requests.post")
    def test_returns_cached_token_without_refetch(self, mock_post):
        _token_cache["cache-key-2"] = ("cached-token", time.time() + 3600)

        token = get_mcp_bearer_token("client-1", "secret-1", "example.auth.region.amazoncognito.com", "cache-key-2")

        self.assertEqual(token, "cached-token")
        mock_post.assert_not_called()

    @patch("app.strands_integration.tools.mcp_tools.requests.post")
    def test_refetches_when_near_expiry(self, mock_post):
        _token_cache["cache-key-3"] = ("stale-token", time.time() + 30)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "fresh-token",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        token = get_mcp_bearer_token("client-1", "secret-1", "example.auth.region.amazoncognito.com", "cache-key-3")

        self.assertEqual(token, "fresh-token")
        mock_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && python -m pytest tests/test_strands_integration/test_mcp_tools.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'app.strands_integration.tools.mcp_tools'`)

- [ ] **Step 3: `mcp_tools.py` にトークン取得・キャッシュを実装する**

`backend/app/strands_integration/tools/mcp_tools.py` を新規作成:

```python
"""
MCP server integration - token acquisition/caching and tool scoping.
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Lambda container in-memory cache: cache_key -> (access_token, expires_at)
_token_cache: dict[str, tuple[str, float]] = {}

TOKEN_REFRESH_MARGIN_SECONDS = 90


def get_mcp_bearer_token(
    client_id: str, client_secret: str, cognito_domain: str, cache_key: str
) -> str:
    """Get a Cognito client_credentials access token, using an in-memory cache.

    Args:
        client_id: Cognito app client ID
        client_secret: Cognito app client secret
        cognito_domain: Cognito hosted UI domain (e.g. "knowledge-mcp-auth.auth.ap-northeast-1.amazoncognito.com")
        cache_key: cache key that uniquely identifies this KB's credentials (the secret ARN)

    Returns:
        str: Bearer access token
    """
    cached = _token_cache.get(cache_key)
    if cached and cached[1] > time.time() + TOKEN_REFRESH_MARGIN_SECONDS:
        return cached[0]

    response = requests.post(
        f"https://{cognito_domain}/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials", "scope": "knowledge-mcp/invoke"},
    )
    response.raise_for_status()
    body = response.json()

    expires_at = time.time() + body["expires_in"]
    _token_cache[cache_key] = (body["access_token"], expires_at)

    return body["access_token"]
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && python -m pytest tests/test_strands_integration/test_mcp_tools.py -v`
Expected: PASS(3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/strands_integration/tools/mcp_tools.py backend/tests/test_strands_integration/test_mcp_tools.py
git commit -m "feat: add Cognito client_credentials token fetch with in-memory caching for MCP"
```

---

### Task 5: MCPツールスコープ(接続ライフサイクル管理)

**Files:**
- Modify: `backend/app/strands_integration/tools/mcp_tools.py`
- Test: `backend/tests/test_strands_integration/test_mcp_tools.py`

**Interfaces:**
- Consumes: `get_mcp_bearer_token`(Task 4), `BotModel`(`backend/app/repositories/models/custom_bot.py`)
- Produces: `mcp_tools_scope(bot: BotModel | None)`(コンテキストマネージャ、`list[StrandsAgentTool]` をyield)

**事前確認:** このタスクはstrands-agents SDKのMCPクライアントAPI(`strands.tools.mcp.MCPClient`、`mcp.client.streamable_http.streamablehttp_client`)に依存する。実装前に以下を実行し、import経路が想定通りか確認すること。もし異なる場合は、実際にインストールされているモジュール構成に合わせて `import` 文を調整する。

Run: `cd backend && python -c "from strands.tools.mcp import MCPClient; from mcp.client.streamable_http import streamablehttp_client; print('ok')"`
Expected: `ok`(NGの場合は `python -c "import strands.tools.mcp as m; print(dir(m))"` および `python -c "import mcp.client; help(mcp.client)"` で実際のエクスポート名を確認し、以降のコードのimportを合わせる)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_strands_integration/test_mcp_tools.py` の末尾(既存の `TestGetMcpBearerToken` クラスの後、`if __name__ == "__main__":` の手前)に追加:

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


class TestMcpToolsScope(unittest.TestCase):
    def test_yields_empty_list_when_bot_is_none(self):
        with mcp_tools_scope(None) as tools:
            self.assertEqual(tools, [])

    def test_yields_empty_list_when_no_mcp_tool_configured(self):
        bot = _make_bot([])
        with mcp_tools_scope(bot) as tools:
            self.assertEqual(tools, [])

    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_yields_empty_list_on_connection_failure(self, mock_get_token):
        mock_get_token.side_effect = Exception("token fetch failed")
        bot = _make_bot(
            [
                McpToolModel(
                    tool_type="mcp",
                    name="mcp",
                    description="MCP knowledge search",
                    mcpConfig=McpConfigModel(
                        endpoint_url="https://example.com/mcp",
                        client_id="client-1",
                        secret_arn="arn:aws:secretsmanager:ap-northeast-1:111111111111:secret:mcp/test-user/test-bot",
                        client_secret="s3cr3t",
                    ),
                )
            ]
        )

        with mcp_tools_scope(bot) as tools:
            self.assertEqual(tools, [])
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && python -m pytest tests/test_strands_integration/test_mcp_tools.py -v`
Expected: FAIL(`ImportError: cannot import name 'mcp_tools_scope'`)

- [ ] **Step 3: `mcp_tools_scope` を実装する**

`backend/app/strands_integration/tools/mcp_tools.py` の冒頭のimportを以下に置き換える:

```python
"""
MCP server integration - token acquisition/caching and tool scoping.
"""

import logging
import os
import time
from contextlib import contextmanager

import requests
from app.repositories.models.custom_bot import BotModel
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
COGNITO_MCP_AUTH_DOMAIN = f"knowledge-mcp-auth.auth.{BEDROCK_REGION}.amazoncognito.com"
```

同ファイルの末尾に追加:

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
            )
        )
        with client:
            yield client.list_tools_sync()
    except Exception as e:
        logger.error(f"MCP connection failed, falling back without MCP tools: {e}")
        yield []
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && python -m pytest tests/test_strands_integration/test_mcp_tools.py -v`
Expected: PASS(6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/strands_integration/tools/mcp_tools.py backend/tests/test_strands_integration/test_mcp_tools.py
git commit -m "feat: add per-turn MCP client scope with graceful degradation"
```

---

### Task 6: 利用可能ツール一覧・bot設定へのmcp配線

**Files:**
- Modify: `backend/app/strands_integration/utils.py`
- Modify: `backend/app/usecases/bot.py`

**Interfaces:**
- Consumes: `_get_mcp_tool_config`(Task 5、`app.strands_integration.tools.mcp_tools`からimport可能にする必要はなく、utils.py側では既存の `get_strands_tools` ループ内でtool_typeを直接見るだけでよい)
- Produces: `get_strands_registered_tools()` が `mcp` プレースホルダーツールを含む、`get_strands_tools()` が `mcp` 設定を実際のツールマッチング対象から除外する(実ツールはTask 7で `extra_tools` 経由で追加されるため)、`fetch_available_agent_tools()` が `McpTool` を返す

- [ ] **Step 1: `get_strands_registered_tools` にmcpプレースホルダーを追加する**

`backend/app/strands_integration/utils.py` の17-30行目を以下に置き換える:

```python
def get_strands_registered_tools(bot: BotModel | None = None) -> list[StrandsAgentTool]:
    """Get list of available Strands tools."""
    from app.strands_integration.tools.bedrock_agent import create_bedrock_agent_tool
    from app.strands_integration.tools.calculator import create_calculator_tool
    from app.strands_integration.tools.internet_search import (
        create_internet_search_tool,
    )
    from app.strands_integration.tools.mcp_placeholder import create_mcp_placeholder_tool
    from app.strands_integration.tools.simple_list import simple_list, structured_list

    tools: list[StrandsAgentTool] = []
    tools.append(create_internet_search_tool(bot))
    tools.append(create_bedrock_agent_tool(bot))
    tools.append(create_mcp_placeholder_tool())
    # tools.append(create_calculator_tool(bot))  # For testing purposes
    return tools
```

`backend/app/strands_integration/tools/mcp_placeholder.py` を新規作成(この関数は「利用可能ツール一覧」にmcpという名前・説明を表示させるためだけの静的プレースホルダーで、実際にAgentへ渡されることはない。Task 6 Step 2で `get_strands_tools()` が `tool_type == "mcp"` のbot設定をこの登録済みツールとのマッチング対象から除外するため):

```python
"""
Static placeholder for the MCP tool catalog entry.

This tool is never actually invoked: when a bot has an `mcp` tool configured,
the real tools are fetched live from the MCP server via
`app.strands_integration.tools.mcp_tools.mcp_tools_scope` and passed to the
Agent as `extra_tools`. This placeholder exists only so `mcp` shows up with a
name/description in the bot-creation "available tools" list.
"""

from strands import tool
from strands.types.tools import AgentTool as StrandsAgentTool


def create_mcp_placeholder_tool() -> StrandsAgentTool:
    @tool
    def mcp() -> dict:
        """
        Connect to an MCP server configured for this bot to search its knowledge base.
        """
        return {
            "status": "error",
            "content": [
                {
                    "text": "This is a placeholder tool and should never be invoked directly."
                }
            ],
        }

    return mcp
```

- [ ] **Step 2: `get_strands_tools` でmcp設定を除外する**

`backend/app/strands_integration/utils.py` の `get_strands_tools` 内、既存の

```python
    # Get tools based on bot's tool configuration
    for tool in bot.agent.tools:
        if tool.name not in [t.tool_name for t in registered_tools]:
            continue
```

を以下に置き換える:

```python
    # Get tools based on bot's tool configuration
    for tool in bot.agent.tools:
        if tool.tool_type == "mcp":
            # MCP tools are fetched live and added separately via
            # `mcp_tools_scope` + `create_strands_agent(extra_tools=...)`.
            continue

        if tool.name not in [t.tool_name for t in registered_tools]:
            continue
```

- [ ] **Step 3: `fetch_available_agent_tools` に `mcp` 分岐を追加する**

`backend/app/usecases/bot.py` の682-706行目、`bedrock_agent` 分岐の後(`elif tool.tool_name == "internet_search":` の手前)に追加:

```python
            elif tool.tool_name == "mcp":
                result.append(
                    McpTool(
                        tool_type="mcp",
                        name=tool.tool_name,
                        description=description,
                    )
                )
```

ファイル冒頭のimportに `McpTool` を追加する(`BedrockAgentTool` がimportされている行を確認し、同じ場所に追加):

```python
from app.routes.schemas.bot import (
    BedrockAgentTool,
    ...
    McpTool,
    ...
)
```

(実際の既存import文の構造は `backend/app/usecases/bot.py` の先頭を確認し、アルファベット順の既存スタイルに合わせて挿入すること)

- [ ] **Step 4: 既存のstrands統合テストにデグレがないことを確認**

Run: `cd backend && python -m pytest tests/test_strands_integration/ -v -k "not TestBedrockAgentTool"`
Expected: PASS(`TestBedrockAgentTool` は実AWSリソースが必要なため除外。それ以外はPASS)

- [ ] **Step 5: Commit**

```bash
git add backend/app/strands_integration/utils.py backend/app/strands_integration/tools/mcp_placeholder.py backend/app/usecases/bot.py
git commit -m "feat: expose mcp tool in available-tools catalog and bot tool matching"
```

---

### Task 7: Strands Agent実行フローへの統合

**Files:**
- Modify: `backend/app/strands_integration/agent/factory.py`
- Modify: `backend/app/strands_integration/chat_strands.py`

**Interfaces:**
- Consumes: `mcp_tools_scope(bot: BotModel | None)`(Task 5)
- Produces: `create_strands_agent(..., extra_tools: list[StrandsAgentTool] | None = None)`

- [ ] **Step 1: `create_strands_agent` に `extra_tools` 引数を追加する**

`backend/app/strands_integration/agent/factory.py` の関数シグネチャ:

```python
def create_strands_agent(
    bot: BotModel | None,
    instructions: list[str],
    model_name: type_model_name,
    generation_params: GenerationParamsModel | None = None,
    guardrail: BedrockGuardrailsModel | None = None,
    enable_reasoning: bool = False,
    prompt_caching_enabled: bool = False,
    has_tools: bool = False,
    hooks: list[HookProvider] | None = None,
) -> Agent:
```

を以下に置き換える:

```python
def create_strands_agent(
    bot: BotModel | None,
    instructions: list[str],
    model_name: type_model_name,
    generation_params: GenerationParamsModel | None = None,
    guardrail: BedrockGuardrailsModel | None = None,
    enable_reasoning: bool = False,
    prompt_caching_enabled: bool = False,
    has_tools: bool = False,
    hooks: list[HookProvider] | None = None,
    extra_tools: list | None = None,
) -> Agent:
```

そして、既存の

```python
    agent = Agent(
        model=model,
        tools=get_strands_tools(bot, model_name),  # type: ignore
        hooks=hooks or [],
        system_prompt=system_prompt,
    )
    return agent
```

を以下に置き換える:

```python
    agent = Agent(
        model=model,
        tools=get_strands_tools(bot, model_name) + (extra_tools or []),  # type: ignore
        hooks=hooks or [],
        system_prompt=system_prompt,
    )
    return agent
```

- [ ] **Step 2: `converse_with_strands` を `mcp_tools_scope` で包む**

`backend/app/strands_integration/chat_strands.py` の冒頭のimportに追加(`from app.strands_integration.agent import create_strands_agent` の直後):

```python
from app.strands_integration.tools.mcp_tools import mcp_tools_scope
```

`converse_with_strands` 内、既存の

```python
    agent = create_strands_agent(
        bot=bot,
        instructions=instructions,
        model_name=chat_input.message.model,
        generation_params=generation_params,
        guardrail=guardrail,
        enable_reasoning=chat_input.enable_reasoning,
        prompt_caching_enabled=prompt_caching_enabled,
        has_tools=has_tools,
        hooks=[tool_capture],
    )

    thinking_log: list[SimpleMessageModel] = []

    def on_message(message: Message):
        if any(
            "toolUse" in content or "toolResult" in content
            for content in message["content"]
        ):
            thinking_log.append(strands_message_to_simple_message_model(message))

    agent.callback_handler = create_callback_handler(
        on_stream=on_stream,
        on_reasoning=on_reasoning,
        on_message=on_message,
    )

    # Convert SimpleMessageModel list to Strands Messages format
    strands_messages = simple_message_models_to_strands_messages(
        simple_messages=messages,
        model=chat_input.message.model,
        guardrail=guardrail,
        search_results=search_results,
        prompt_caching_enabled=prompt_caching_enabled,
    )

    def run_agent(agent: Agent) -> tuple[StopReason, Message, EventLoopMetrics]:
        try:
            result = agent(strands_messages)
            return (
                result.stop_reason,
                result.message,
                result.metrics,
            )

        except MaxTokensReachedException:
            return (
                "max_tokens",
                agent.messages[-1],
                agent.event_loop_metrics,
            )

    stop_reason, result_message, metrics = run_agent(agent)
```

を以下に置き換える:

```python
    with mcp_tools_scope(bot) as mcp_tools:
        agent = create_strands_agent(
            bot=bot,
            instructions=instructions,
            model_name=chat_input.message.model,
            generation_params=generation_params,
            guardrail=guardrail,
            enable_reasoning=chat_input.enable_reasoning,
            prompt_caching_enabled=prompt_caching_enabled,
            has_tools=has_tools,
            hooks=[tool_capture],
            extra_tools=mcp_tools,
        )

        thinking_log: list[SimpleMessageModel] = []

        def on_message(message: Message):
            if any(
                "toolUse" in content or "toolResult" in content
                for content in message["content"]
            ):
                thinking_log.append(strands_message_to_simple_message_model(message))

        agent.callback_handler = create_callback_handler(
            on_stream=on_stream,
            on_reasoning=on_reasoning,
            on_message=on_message,
        )

        # Convert SimpleMessageModel list to Strands Messages format
        strands_messages = simple_message_models_to_strands_messages(
            simple_messages=messages,
            model=chat_input.message.model,
            guardrail=guardrail,
            search_results=search_results,
            prompt_caching_enabled=prompt_caching_enabled,
        )

        def run_agent(agent: Agent) -> tuple[StopReason, Message, EventLoopMetrics]:
            try:
                result = agent(strands_messages)
                return (
                    result.stop_reason,
                    result.message,
                    result.metrics,
                )

            except MaxTokensReachedException:
                return (
                    "max_tokens",
                    agent.messages[-1],
                    agent.event_loop_metrics,
                )

        stop_reason, result_message, metrics = run_agent(agent)
```

(このブロック以降、`converse_with_strands` の残りの処理 — `message = strands_message_to_message_model(...)` から関数末尾の `return OnStopInput(...)` まで — は `with` ブロックの外側のままでよい。MCP接続は `agent(strands_messages)` の実行が終わった時点でcloseされていればよく、レスポンス組み立て自体はMCP接続に依存しないため)

- [ ] **Step 3: 既存のstrands統合テスト・型チェックにデグレがないことを確認**

Run: `cd backend && python -m pytest tests/test_strands_integration/ -v -k "not TestBedrockAgentTool"`
Expected: PASS

Run: `cd backend && python -c "import app.strands_integration.chat_strands; import app.strands_integration.agent.factory; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/app/strands_integration/agent/factory.py backend/app/strands_integration/chat_strands.py
git commit -m "feat: wire per-turn MCP tools into the Strands agent execution flow"
```

---

### Task 8: フロントエンド型定義・型ガード追加

**Files:**
- Modify: `frontend/src/features/agent/types/index.d.ts`
- Modify: `frontend/src/features/agent/utils/typeGuards.ts`

**Interfaces:**
- Produces: `McpConfig`(型)、`McpAgentTool`(型)、更新後の `ToolType` / `AgentTool`、`isMcpTool(tool: AgentTool): tool is McpAgentTool`

- [ ] **Step 1: 型定義を追加する**

`frontend/src/features/agent/types/index.d.ts` の

```typescript
export type SearchEngine = 'duckduckgo' | 'firecrawl';
export type ToolType = 'internet' | 'plain' | 'bedrock_agent';

export type BedrockAgentConfig = {
  agentId: string;
  aliasId: string;
};
```

を以下に置き換える:

```typescript
export type SearchEngine = 'duckduckgo' | 'firecrawl';
export type ToolType = 'internet' | 'plain' | 'bedrock_agent' | 'mcp';

export type BedrockAgentConfig = {
  agentId: string;
  aliasId: string;
};

export type McpConfig = {
  endpointUrl: string;
  clientId: string;
  clientSecret: string;
};
```

そして

```typescript
export type BedrockAgentTool = {
  toolType: 'bedrock_agent';
  name: string;
  description: string;
  bedrockAgentConfig?: BedrockAgentConfig;
};

export type AgentTool = InternetAgentTool | PlainAgentTool | BedrockAgentTool;
```

を以下に置き換える:

```typescript
export type BedrockAgentTool = {
  toolType: 'bedrock_agent';
  name: string;
  description: string;
  bedrockAgentConfig?: BedrockAgentConfig;
};

export type McpAgentTool = {
  toolType: 'mcp';
  name: string;
  description: string;
  mcpConfig?: McpConfig;
};

export type AgentTool =
  | InternetAgentTool
  | PlainAgentTool
  | BedrockAgentTool
  | McpAgentTool;
```

- [ ] **Step 2: 型ガードを追加する**

`frontend/src/features/agent/utils/typeGuards.ts` を以下に置き換える:

```typescript
import { AgentTool, InternetAgentTool, BedrockAgentTool, McpAgentTool } from '../types';

export const isInternetTool = (tool: AgentTool): tool is InternetAgentTool =>
  tool.toolType === 'internet';

export const isBedrockAgentTool = (tool: AgentTool): tool is BedrockAgentTool =>
  tool.toolType === 'bedrock_agent';

export const isMcpTool = (tool: AgentTool): tool is McpAgentTool =>
  tool.toolType === 'mcp';
```

- [ ] **Step 3: TypeScriptの型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラーなし(既存の `BedrockAgentConfig.tsx` 等が新しい `AgentTool` unionを網羅していないことによるエラーが出ないか確認。もし出た場合はTask 9・10で解消される)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/agent/types/index.d.ts frontend/src/features/agent/utils/typeGuards.ts
git commit -m "feat: add MCP tool frontend types and type guard"
```

---

### Task 9: MCP設定入力コンポーネントとi18n

**Files:**
- Create: `frontend/src/features/agent/components/McpConfig.tsx`
- Modify: `frontend/src/i18n/ja/index.ts`
- Modify: `frontend/src/i18n/en/index.ts`
- Modify: `frontend/src/i18n/pt-br/index.ts`

**Interfaces:**
- Consumes: `McpConfig`型(Task 8)、`InputText`コンポーネント(`frontend/src/components/InputText.tsx`)
- Produces: `McpConfig`コンポーネント(`{ config: McpConfigType; onChange: (config: McpConfigType) => void }`)、i18nキー `agent.tools.mcp.{name,description}`、`agent.tools.mcpConfig.{endpointUrl,clientId,clientSecret}.{label,placeholder}`

- [ ] **Step 1: `McpConfig.tsx` を新規作成する**

`frontend/src/features/agent/components/McpConfig.tsx`:

```typescript
import { useTranslation } from 'react-i18next';
import InputText from '../../../components/InputText';
import { McpConfig as McpConfigType } from '../types';

type Props = {
  config: McpConfigType;
  onChange: (config: McpConfigType) => void;
};

export const McpConfig = ({ config, onChange }: Props) => {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      <InputText
        label={t('agent.tools.mcpConfig.endpointUrl.label')}
        placeholder={t('agent.tools.mcpConfig.endpointUrl.placeholder')}
        value={config.endpointUrl}
        onChange={(value) => onChange({ ...config, endpointUrl: value })}
      />
      <InputText
        label={t('agent.tools.mcpConfig.clientId.label')}
        placeholder={t('agent.tools.mcpConfig.clientId.placeholder')}
        value={config.clientId}
        onChange={(value) => onChange({ ...config, clientId: value })}
      />
      <InputText
        type="password"
        label={t('agent.tools.mcpConfig.clientSecret.label')}
        placeholder={t('agent.tools.mcpConfig.clientSecret.placeholder')}
        value={config.clientSecret}
        onChange={(value) => onChange({ ...config, clientSecret: value })}
      />
    </div>
  );
};
```

- [ ] **Step 2: 日本語i18nキーを追加する**

`frontend/src/i18n/ja/index.ts` の

```typescript
        bedrockAgent: {
          name: 'Bedrock Agent',
          description: 'Bedrock Agentをツールとして使用します。',
          agentId: {
            label: 'Agent ID',
            placeholder: 'Agent IDを入力',
          },
          aliasId: {
            label: 'Alias ID',
            placeholder: 'Alias IDを入力',
          },
        },
      },
    },
    bot: {
```

を以下に置き換える(`bedrockAgent` ブロックの後に `mcp` と `mcpConfig` を追加):

```typescript
        bedrockAgent: {
          name: 'Bedrock Agent',
          description: 'Bedrock Agentをツールとして使用します。',
          agentId: {
            label: 'Agent ID',
            placeholder: 'Agent IDを入力',
          },
          aliasId: {
            label: 'Alias ID',
            placeholder: 'Alias IDを入力',
          },
        },
        mcp: {
          name: 'MCPサーバー',
          description: '設定されたMCPサーバーに接続してツールを利用します。',
        },
        mcpConfig: {
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
        },
      },
    },
    bot: {
```

- [ ] **Step 3: 英語i18nキーを追加する**

`frontend/src/i18n/en/index.ts` の

```typescript
        bedrockAgent: {
          name: 'Bedrock Agent',
          description: 'Use Bedrock Agent as a tool.',
          agentId: {
            label: 'Agent ID',
            placeholder: 'Enter Agent ID',
          },
          aliasId: {
            label: 'Alias ID',
            placeholder: 'Enter Alias ID',
          },
        },
      },
```

を以下に置き換える:

```typescript
        bedrockAgent: {
          name: 'Bedrock Agent',
          description: 'Use Bedrock Agent as a tool.',
          agentId: {
            label: 'Agent ID',
            placeholder: 'Enter Agent ID',
          },
          aliasId: {
            label: 'Alias ID',
            placeholder: 'Enter Alias ID',
          },
        },
        mcp: {
          name: 'MCP Server',
          description: 'Connect to the configured MCP server to use its tools.',
        },
        mcpConfig: {
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
        },
      },
```

- [ ] **Step 4: ポルトガル語i18nキーを追加する**

`frontend/src/i18n/pt-br/index.ts` の

```typescript
        bedrockAgent: {
          name: 'Agente Bedrock',
          description: 'Use o Agente Bedrock como uma ferramenta.',
          agentId: {
            label: 'ID do Agente',
            placeholder: 'Digite o ID do Agente',
          },
          aliasId: {
            label: 'ID do Alias',
            placeholder: 'Digite o ID do Alias',
          },
        },
      },
```

を以下に置き換える:

```typescript
        bedrockAgent: {
          name: 'Agente Bedrock',
          description: 'Use o Agente Bedrock como uma ferramenta.',
          agentId: {
            label: 'ID do Agente',
            placeholder: 'Digite o ID do Agente',
          },
          aliasId: {
            label: 'ID do Alias',
            placeholder: 'Digite o ID do Alias',
          },
        },
        mcp: {
          name: 'Servidor MCP',
          description: 'Conecte-se ao servidor MCP configurado para usar suas ferramentas.',
        },
        mcpConfig: {
          endpointUrl: {
            label: 'URL do Endpoint',
            placeholder: 'Digite a URL do endpoint do servidor MCP',
          },
          clientId: {
            label: 'Client ID',
            placeholder: 'Digite o Client ID',
          },
          clientSecret: {
            label: 'Client Secret',
            placeholder: 'Digite o Client Secret',
          },
        },
      },
```

- [ ] **Step 5: TypeScriptの型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラーなし

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/agent/components/McpConfig.tsx frontend/src/i18n/ja/index.ts frontend/src/i18n/en/index.ts frontend/src/i18n/pt-br/index.ts
git commit -m "feat: add MCP config input component and i18n strings"
```

---

### Task 10: bot作成/編集画面へのMCPツール配線

**Files:**
- Modify: `frontend/src/features/agent/components/AvailableTools.tsx`

**Interfaces:**
- Consumes: `McpConfig`コンポーネント・`isMcpTool`(Task 8, 9)

- [ ] **Step 1: importを追加する**

`frontend/src/features/agent/components/AvailableTools.tsx` の冒頭を以下に置き換える:

```typescript
import { Trans, useTranslation } from 'react-i18next';
import {
  AgentTool,
  BedrockAgentConfig,
  BedrockAgentTool,
  FirecrawlConfig,
  InternetAgentTool,
  McpAgentTool,
  McpConfig as McpConfigType,
  SearchEngine,
  ToolType,
} from '../types';
import { isInternetTool, isBedrockAgentTool, isMcpTool } from '../utils/typeGuards';
import Toggle from '../../../components/Toggle';
import { Dispatch, useCallback, useState, useEffect } from 'react';
import { formatDescription } from '../functions/formatDescription';
import Help from '../../../components/Help';
import Skeleton from '../../../components/Skeleton';
import { TooltipDirection } from '../../../constants';
import { FirecrawlConfig as FirecrawlConfigComponent } from './FirecrawlConfig';
import { BedrockAgentConfig as BedrockAgentConfigComponent } from './BedrockAgentConfig';
import { McpConfig as McpConfigComponent } from './McpConfig';
import ExpandableDrawerGroup from '../../../components/ExpandableDrawerGroup';
import RadioButton from '../../../components/RadioButton';
import { DEFAULT_FIRECRAWL_CONFIG } from '../constants';
```

- [ ] **Step 2: `handleChangeTool` にmcp分岐を追加する**

`handleChangeTool` 内の既存コード(56-86行目):

```typescript
      } else if (tool.name === 'bedrock_agent') {
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
                  toolType: 'bedrock_agent' as ToolType,
                  name: 'bedrock_agent',
                  bedrockAgentConfig: {
                    agentId: '',
                    aliasId: '',
                  },
                } as AgentTool,
              ];

          return newTools;
        });
      } else {
        setTools((preTools) =>
          preTools?.map(({ name }) => name).includes(tool.name)
            ? [...preTools.filter(({ name }) => name != tool.name)]
            : [...preTools, tool]
        );
      }
```

を、以下に置き換える(`bedrock_agent` 分岐はそのまま残し、その直後に `mcp` 分岐を追加する):

```typescript
      } else if (tool.name === 'bedrock_agent') {
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
                  toolType: 'bedrock_agent' as ToolType,
                  name: 'bedrock_agent',
                  bedrockAgentConfig: {
                    agentId: '',
                    aliasId: '',
                  },
                } as AgentTool,
              ];

          return newTools;
        });
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
        setTools((preTools) =>
          preTools?.map(({ name }) => name).includes(tool.name)
            ? [...preTools.filter(({ name }) => name != tool.name)]
            : [...preTools, tool]
        );
      }
```

- [ ] **Step 3: `handleMcpConfigChange` ハンドラを追加する**

`handleBedrockAgentConfigChange` の定義(112-129行目)の直後に追加:

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

- [ ] **Step 4: ツール一覧描画にmcp設定フォームを追加する**

既存の `bedrock_agent` の描画ブロック(276-294行目)の直後、`</div>`(295行目、`tool`のmap内の閉じdiv)の手前に追加:

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

- [ ] **Step 5: TypeScriptの型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラーなし

- [ ] **Step 6: フロントエンドの開発サーバーで手動確認**

Run: `cd frontend && npm run dev`

ブラウザでbot作成画面を開き、「利用可能なツール」一覧に「MCPサーバー」が表示されること、トグルをONにするとエンドポイントURL/Client ID/Client Secretの入力フォームが表示されること、値を入力してbotを保存・再度開いた際に入力値が保持されていることを確認する。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/agent/components/AvailableTools.tsx
git commit -m "feat: wire MCP tool into the bot creation/edit available-tools UI"
```

---

## 実装後の手動確認(結合)

自動テストではカバーできない、実際のacrocity-rag-system MCPサーバーとの疎通確認:

1. bot作成画面でMCPツールを有効化し、`bedrock-chat-mcp-auth.md` に記載のエンドポイントURL・Client ID・Client Secretを入力してbotを保存する
2. そのbotとチャットし、KB検索が必要な質問を投げてMCP経由のツール呼び出しが行われることをログ(CloudWatch Logs)で確認する
3. 意図的にClient Secretを誤った値にしてチャットし、エラーがログに残りつつもチャット自体は他のツール・通常応答で継続することを確認する(縮退運転の確認)

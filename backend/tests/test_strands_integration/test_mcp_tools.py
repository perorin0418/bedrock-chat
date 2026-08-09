import asyncio
import sys

sys.path.append(".")
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import Tool as MCPTool
from strands.tools.mcp import MCPAgentTool

from app.strands_integration.tools.mcp_tools import (
    COGNITO_MCP_AUTH_DOMAIN,
    _LabelStrippingMcpClient,
    _build_auth_headers,
    _token_cache,
    get_mcp_bearer_token,
)


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

        token = get_mcp_bearer_token(
            "client-1",
            "secret-1",
            "example.auth.region.amazoncognito.com",
            "cache-key-1",
        )

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
        self.assertEqual(kwargs["timeout"], 10)

    @patch("app.strands_integration.tools.mcp_tools.requests.post")
    def test_returns_cached_token_without_refetch(self, mock_post):
        _token_cache["cache-key-2"] = ("cached-token", time.time() + 3600)

        token = get_mcp_bearer_token(
            "client-1",
            "secret-1",
            "example.auth.region.amazoncognito.com",
            "cache-key-2",
        )

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

        token = get_mcp_bearer_token(
            "client-1",
            "secret-1",
            "example.auth.region.amazoncognito.com",
            "cache-key-3",
        )

        self.assertEqual(token, "fresh-token")
        mock_post.assert_called_once()


class TestBuildAuthHeaders(unittest.TestCase):
    def test_none_returns_no_headers(self):
        server = McpConfigModel(
            label="public",
            endpoint_url="https://example.com/mcp",
            auth_type=McpAuthType.NONE,
        )

        headers, _auth = _build_auth_headers(
            server,
            secret_arn=None,
            bot_id="bot-1",
            user_id="user-1",
        )

        self.assertEqual(headers, {})

    def test_bearer_token_returns_bearer_header(self):
        server = McpConfigModel(
            label="rovo",
            endpoint_url="https://mcp.atlassian.com/v1/mcp",
            auth_type=McpAuthType.BEARER_TOKEN,
            bearer_token="rovo-token-1",
        )

        headers, _auth = _build_auth_headers(
            server,
            secret_arn=None,
            bot_id="bot-1",
            user_id="user-1",
        )

        self.assertEqual(headers, {"Authorization": "Bearer rovo-token-1"})

    def test_basic_auth_returns_base64_basic_header(self):
        server = McpConfigModel(
            label="rovo",
            endpoint_url="https://mcp.atlassian.com/v1/mcp",
            auth_type=McpAuthType.BASIC_AUTH,
            username="me@example.com",
            basic_auth_token="api-token-1",
        )

        headers, _auth = _build_auth_headers(
            server,
            secret_arn=None,
            bot_id="bot-1",
            user_id="user-1",
        )

        import base64

        expected = base64.b64encode(b"me@example.com:api-token-1").decode()
        self.assertEqual(headers, {"Authorization": f"Basic {expected}"})

    def test_api_key_returns_x_api_key_header(self):
        server = McpConfigModel(
            label="gateway",
            endpoint_url="https://abc123.execute-api.ap-northeast-1.amazonaws.com/prod/mcp",
            auth_type=McpAuthType.API_KEY,
            api_key="apigw-key-1",
        )

        headers, _auth = _build_auth_headers(
            server,
            secret_arn=None,
            bot_id="bot-1",
            user_id="user-1",
        )

        self.assertEqual(headers, {"x-api-key": "apigw-key-1"})

    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_cognito_client_credentials_uses_existing_token_flow(self, mock_get_token):
        mock_get_token.return_value = "cognito-token-1"
        server = McpConfigModel(
            label="powersort",
            endpoint_url="https://example.com/powersort",
            auth_type=McpAuthType.COGNITO_CLIENT_CREDENTIALS,
            client_id="client-1",
            client_secret="secret-1",
        )

        headers, _auth = _build_auth_headers(
            server,
            secret_arn="arn:aws:secretsmanager:...",
            bot_id="bot-1",
            user_id="user-1",
        )

        self.assertEqual(headers, {"Authorization": "Bearer cognito-token-1"})
        mock_get_token.assert_called_once_with(
            "client-1",
            "secret-1",
            COGNITO_MCP_AUTH_DOMAIN,
            "arn:aws:secretsmanager:...:powersort",
        )

    @patch(
        "app.strands_integration.tools.mcp_tools.MCP_OAUTH_REDIRECT_URI",
        "https://api.example.com/mcp/oauth/callback",
    )
    def test_build_auth_headers_oauth_returns_provider(self):
        from app.strands_integration.tools.mcp_tools import _build_auth_headers
        from mcp.client.auth.oauth2 import OAuthClientProvider

        server = MagicMock()
        server.auth_type = McpAuthType.OAUTH
        server.label = "atlassian"
        server.endpoint_url = "https://mcp.atlassian.com/v1/mcp"

        headers, auth = _build_auth_headers(
            server,
            secret_arn=None,
            bot_id="bot-1",
            user_id="user-1",
        )

        self.assertEqual(headers, {})
        self.assertIsInstance(auth, OAuthClientProvider)

    def test_build_auth_headers_bearer_token_unchanged(self):
        from app.strands_integration.tools.mcp_tools import _build_auth_headers

        server = MagicMock()
        server.auth_type = McpAuthType.BEARER_TOKEN
        server.bearer_token = "token-1"

        headers, auth = _build_auth_headers(
            server,
            secret_arn=None,
            bot_id="bot-1",
            user_id="user-1",
        )

        self.assertEqual(headers, {"Authorization": "Bearer token-1"})
        self.assertIsNone(auth)


from app.repositories.models.custom_bot import (
    ActiveModelsModel,
    AgentModel,
    GenerationParamsModel,
    KnowledgeModel,
    McpAuthType,
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
        default_model="claude-v4-opus",
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
        self.mcp_client = MagicMock()


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
    def test_one_server_list_tools_failure_does_not_block_the_others(
        self, mock_get_token, mock_mcp_client_cls
    ):
        mock_get_token.return_value = "token-1"

        bad_client = MagicMock()
        bad_client.list_tools_sync.side_effect = Exception("list_tools failed")

        good_client = MagicMock()
        good_client.list_tools_sync.return_value = [_FakeMcpTool("search")]

        clients = []

        def client_factory(*args, **kwargs):
            if len(clients) == 0:
                clients.append(bad_client)
                return bad_client
            else:
                clients.append(good_client)
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

    @patch("app.strands_integration.tools.mcp_tools.MCPClient")
    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_colliding_prefixed_tool_name_is_skipped_not_raised(
        self, mock_get_token, mock_mcp_client_cls
    ):
        """Two servers whose renamed tools collide on the final prefixed name
        must not raise: the later duplicate is silently dropped and the first
        one wins, so the chat turn keeps working instead of crashing."""
        mock_get_token.return_value = "token-1"

        client_one = MagicMock()
        client_one.list_tools_sync.return_value = [_FakeMcpTool("bar_search")]

        client_two = MagicMock()
        client_two.list_tools_sync.return_value = [_FakeMcpTool("search")]

        clients = [client_one, client_two]

        def client_factory(*args, **kwargs):
            return clients.pop(0)

        mock_mcp_client_cls.side_effect = client_factory

        # "foo" + "bar_search" and "foo_bar" + "search" both produce
        # "foo_bar_search" after prefixing.
        bot = _make_bot(
            [
                _make_mcp_tool(
                    _make_server("foo", client_id="client-1"),
                    _make_server("foo_bar", client_id="client-2"),
                )
            ]
        )

        with mcp_tools_scope(bot) as tools:
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0].mcp_tool.name, "foo_bar_search")

    @patch("app.strands_integration.tools.mcp_tools.MCPClient")
    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_colliding_with_base_tool_name_is_skipped_not_raised(
        self, mock_get_token, mock_mcp_client_cls
    ):
        """A renamed MCP tool that matches a reserved base (non-MCP) tool
        name -- e.g. label "internet" + tool "search" -> "internet_search",
        colliding with the built-in internet_search tool -- must be skipped
        (logged) rather than crashing `Agent(tools=...)` construction with a
        duplicate-name error."""
        mock_get_token.return_value = "token-1"
        mock_client_instance = MagicMock()
        mock_client_instance.list_tools_sync.return_value = [_FakeMcpTool("search")]
        mock_mcp_client_cls.return_value = mock_client_instance

        bot = _make_bot([_make_mcp_tool(_make_server("internet"))])

        with mcp_tools_scope(bot) as tools:
            self.assertEqual(tools, [])

    @patch("app.strands_integration.tools.mcp_tools.MCPClient")
    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_hyphen_underscore_collision_is_skipped_not_raised(
        self, mock_get_token, mock_mcp_client_cls
    ):
        """`strands`'s tool registry treats `-` and `_` as equivalent when
        detecting name collisions (ToolRegistry.register_tool), so two MCP
        tools whose prefixed names differ only by hyphen vs underscore must
        still be deduplicated instead of both being kept and crashing later
        at `Agent(tools=...)` construction time."""
        mock_get_token.return_value = "token-1"

        client_one = MagicMock()
        client_one.list_tools_sync.return_value = [_FakeMcpTool("bar-search")]

        client_two = MagicMock()
        client_two.list_tools_sync.return_value = [_FakeMcpTool("search")]

        clients = [client_one, client_two]

        def client_factory(*args, **kwargs):
            return clients.pop(0)

        mock_mcp_client_cls.side_effect = client_factory

        # "foo" + "bar-search" -> "foo_bar-search"
        # "foo_bar" + "search" -> "foo_bar_search"
        # These are different raw strings but normalize (- -> _) to the same
        # name, so only the first one should survive.
        bot = _make_bot(
            [
                _make_mcp_tool(
                    _make_server("foo", client_id="client-1"),
                    _make_server("foo_bar", client_id="client-2"),
                )
            ]
        )

        with mcp_tools_scope(bot) as tools:
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0].mcp_tool.name, "foo_bar-search")

    @patch("app.strands_integration.tools.mcp_tools.streamablehttp_client")
    @patch("app.strands_integration.tools.mcp_tools.MCPClient")
    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_mixed_auth_types_send_correct_headers_per_server(
        self, mock_get_token, mock_mcp_client_cls, mock_streamablehttp_client
    ):
        """Three servers with different auth_types in one bot: verify each
        server's MCPClient transport callable is built with THAT server's
        headers, not a later iteration's (headers cross-wiring would be a
        credential leak between MCP servers)."""
        mock_get_token.return_value = "cognito-token-1"

        captured_transport_callables = []

        def client_factory(transport_callable, **kwargs):
            captured_transport_callables.append(transport_callable)
            mock_instance = MagicMock()
            mock_instance.list_tools_sync.return_value = []
            return mock_instance

        mock_mcp_client_cls.side_effect = client_factory

        none_server = McpConfigModel(
            label="public",
            endpoint_url="https://example.com/public",
            auth_type=McpAuthType.NONE,
        )
        bearer_server = McpConfigModel(
            label="rovo",
            endpoint_url="https://mcp.atlassian.com/v1/mcp",
            auth_type=McpAuthType.BEARER_TOKEN,
            bearer_token="rovo-token-1",
        )
        cognito_server = _make_server("powersort")

        bot = _make_bot([_make_mcp_tool(none_server, bearer_server, cognito_server)])

        with mcp_tools_scope(bot) as tools:
            self.assertEqual(tools, [])

        self.assertEqual(len(captured_transport_callables), 3)
        for transport_callable in captured_transport_callables:
            transport_callable()

        self.assertEqual(mock_streamablehttp_client.call_count, 3)
        calls_by_endpoint = {
            call.args[0]: call.kwargs["headers"]
            for call in mock_streamablehttp_client.call_args_list
        }
        self.assertEqual(calls_by_endpoint["https://example.com/public"], {})
        self.assertEqual(
            calls_by_endpoint["https://mcp.atlassian.com/v1/mcp"],
            {"Authorization": "Bearer rovo-token-1"},
        )
        self.assertEqual(
            calls_by_endpoint["https://example.com/mcp"],
            {"Authorization": "Bearer cognito-token-1"},
        )


class TestLabelStrippingMcpClient(unittest.TestCase):
    """Verifies the fix for the tool-name-prefixing bug: the model-facing
    tool name stays prefixed, but the name actually sent to the MCP server
    over the wire must be the original, unprefixed name. Uses a real
    MCPAgentTool (not the _FakeMcpTool stand-in) so that MCPAgentTool.stream()
    -> mcp_client.call_tool_async() is actually exercised end to end."""

    def test_prefixed_tool_invokes_server_with_original_name(self):
        raw_tool = MCPTool(
            name="search", description="d", inputSchema={"type": "object"}
        )
        fake_client = MagicMock()
        fake_client.call_tool_async = AsyncMock(return_value="fake-result")
        agent_tool = MCPAgentTool(raw_tool, fake_client)

        # apply the same renaming/wrapping the production code does
        prefix = "powersort_"
        agent_tool.mcp_tool.name = f"{prefix}{agent_tool.mcp_tool.name}"
        agent_tool.mcp_client = _LabelStrippingMcpClient(agent_tool.mcp_client, prefix)

        self.assertEqual(agent_tool.tool_name, "powersort_search")
        self.assertEqual(agent_tool.tool_spec["name"], "powersort_search")

        async def run():
            events = []
            async for event in agent_tool.stream({"toolUseId": "t1", "input": {}}, {}):
                events.append(event)
            return events

        asyncio.run(run())

        fake_client.call_tool_async.assert_called_once()
        call_kwargs = fake_client.call_tool_async.call_args.kwargs
        # the name reaching the underlying (unwrapped) client must be the
        # original "search", not the model-facing "powersort_search"
        self.assertEqual(call_kwargs["name"], "search")
        self.assertEqual(call_kwargs["tool_use_id"], "t1")


if __name__ == "__main__":
    unittest.main()

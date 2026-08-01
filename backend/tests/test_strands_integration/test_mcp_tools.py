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

    @patch("app.strands_integration.tools.mcp_tools.MCPClient")
    @patch("app.strands_integration.tools.mcp_tools.get_mcp_bearer_token")
    def test_body_exception_propagates_unchanged(
        self, mock_get_token, mock_mcp_client_cls
    ):
        mock_get_token.return_value = "token-1"
        mock_client_instance = MagicMock()
        mock_client_instance.list_tools_sync.return_value = []
        mock_mcp_client_cls.return_value = mock_client_instance

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


if __name__ == "__main__":
    unittest.main()

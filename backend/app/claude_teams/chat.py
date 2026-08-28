"""Chat execution engine for `claude-teams-*` models: runs the real Claude
Code CLI binary (via `claude-agent-sdk`) authenticated with a
`CLAUDE_CODE_OAUTH_TOKEN` drawn from the token pool, instead of talking to
Amazon Bedrock.
"""

import asyncio
import logging
import time
from typing import Callable, TypedDict

from app.agents.tools.agent_tool import ToolRunResult
from app.claude_teams.errors import (
    ClaudeTeamsAllTokensUnavailableError,
    ClaudeTeamsExecutionError,
    classify_api_retry_error,
)
from app.claude_teams.mcp_bridge import build_claude_teams_mcp_servers
from app.claude_teams.models import get_claude_teams_native_model_id
from app.claude_teams.token_repository import disable_token, pick_next_available_token, set_cooldown
from app.claude_teams.token_secrets import get_claude_teams_token
from app.claude_teams.workspace import claude_teams_workspace
from app.repositories.models.conversation import SimpleMessageModel, TextContentModel
from app.repositories.models.custom_bot import BotModel
from app.routes.schemas.conversation import ChatInput
from app.stream import OnStopInput, OnThinking
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Tools the CLI subprocess must never be allowed to use for a chat request:
# local filesystem/shell/network primitives, as opposed to the in-process
# MCP tools bridged in via `build_claude_teams_mcp_servers`.
DISALLOWED_LOCAL_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
]

# How long a token stays in cooldown after a rate_limit/billing_error
# response, before it's tried again.
COOLDOWN_SECONDS = 5 * 60


class _RunResult(TypedDict):
    text: str
    input_tokens: int
    output_tokens: int


def _simple_messages_to_prompt(messages: list[SimpleMessageModel]) -> str:
    """Flatten the conversation history into a single prompt string.

    Each turn is rendered as "User: ..." / "Assistant: ..." so the CLI sees
    the full conversation on every call (the CLI session itself is
    disposable — no --resume/session_store is used, per the architecture
    doc). Only text content is included; other content types are not
    supported for Claude Teams chats in this version.
    """
    lines: list[str] = []
    for message in messages:
        role_label = "User" if message.role == "user" else "Assistant"
        text_parts = [
            content.body
            for content in message.content
            if isinstance(content, TextContentModel)
        ]
        if text_parts:
            lines.append(f"{role_label}: {' '.join(text_parts)}")
    return "\n\n".join(lines)


async def _run_claude_query(
    prompt: str,
    model_id: str,
    oauth_token: str,
    workspace_dir: str,
    mcp_servers: dict,
    allowed_tools: list[str],
    on_stream: Callable[[str], None] | None,
    on_reasoning: Callable[[str], None] | None,
) -> _RunResult:
    """Run one Claude Code CLI query via claude-agent-sdk and collect the
    final text + token usage. Raises `ClaudeTeamsExecutionError` for
    passthrough failures, or a tuple-carrying exception classified by the
    caller for cooldown/disable-worthy failures (see `_ApiRetryFailure`)."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        SystemMessage,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(
        model=model_id,
        cwd=workspace_dir,
        env={"CLAUDE_CODE_OAUTH_TOKEN": oauth_token, "CLAUDE_CONFIG_DIR": workspace_dir},
        disallowed_tools=DISALLOWED_LOCAL_TOOLS,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
    )

    text_parts: list[str] = []
    input_tokens = 0
    output_tokens = 0
    last_error_kind: str | None = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                    if on_stream:
                        on_stream(block.text)
        elif isinstance(message, SystemMessage) and message.subtype == "api_retry":
            last_error_kind = message.data.get("error", "unknown")
        elif isinstance(message, ResultMessage):
            usage = message.usage or {}
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            if message.subtype != "success":
                if last_error_kind:
                    raise _ApiRetryFailure(last_error_kind)
                raise ClaudeTeamsExecutionError(
                    f"Claude Code CLI run failed: subtype={message.subtype}"
                )

    return _RunResult(
        text="".join(text_parts), input_tokens=input_tokens, output_tokens=output_tokens
    )


class _ApiRetryFailure(Exception):
    """Internal-only: wraps a classified `system/api_retry` error kind so
    `converse_with_claude_teams` can act on the token pool before
    re-raising (or falling back) for the caller."""

    def __init__(self, error_kind: str):
        super().__init__(error_kind)
        self.error_kind = error_kind


def converse_with_claude_teams(
    bot: BotModel | None,
    chat_input: ChatInput,
    instructions: list[str],
    messages: list[SimpleMessageModel],
    on_stream: Callable[[str], None] | None = None,
    on_thinking: Callable[[OnThinking], None] | None = None,
    on_tool_result: Callable[[ToolRunResult], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> OnStopInput:
    """Chat with a `claude-teams-*` model via the Claude Code CLI, retrying
    across the token pool on cooldown/disable-worthy failures. Raises
    `ClaudeTeamsAllTokensUnavailableError` if no token is available at all,
    or if every available token fails with a cooldown/disable-worthy error
    in turn."""
    model_id = get_claude_teams_native_model_id(chat_input.message.model)
    prompt = _simple_messages_to_prompt(messages)

    tried_token_ids: set[str] = set()

    while True:
        token = pick_next_available_token()
        if token is None or token.token_id in tried_token_ids:
            raise ClaudeTeamsAllTokensUnavailableError(
                "No Claude Teams OAuth token is currently available."
            )
        tried_token_ids.add(token.token_id)
        oauth_token = get_claude_teams_token(token.token_id)

        with claude_teams_workspace(instructions) as workspace_dir:
            mcp_servers, allowed_tools, mcp_cleanup = build_claude_teams_mcp_servers(
                bot=bot, model_name=chat_input.message.model
            )
            try:
                run_result = asyncio.run(
                    _run_claude_query(
                        prompt=prompt,
                        model_id=model_id,
                        oauth_token=oauth_token,
                        workspace_dir=workspace_dir,
                        mcp_servers=mcp_servers,
                        allowed_tools=allowed_tools,
                        on_stream=on_stream,
                        on_reasoning=on_reasoning,
                    )
                )
            except _ApiRetryFailure as failure:
                action = classify_api_retry_error(failure.error_kind)
                if action == "cooldown":
                    set_cooldown(
                        token.token_id,
                        cooldown_until_epoch_seconds=int(time.time()) + COOLDOWN_SECONDS,
                    )
                    continue
                elif action == "disable":
                    disable_token(token.token_id)
                    continue
                else:
                    raise ClaudeTeamsExecutionError(
                        f"Claude Code CLI error: {failure.error_kind}"
                    ) from failure
            finally:
                mcp_cleanup.close()

        break

    message = _build_message_model(run_result["text"], chat_input.message.model)

    return OnStopInput(
        message=message,
        stop_reason="end_turn",
        input_token_count=run_result["input_tokens"],
        output_token_count=run_result["output_tokens"],
        cache_read_input_count=0,
        cache_write_input_count=0,
        price=0.0,
    )


def _build_message_model(text: str, model_name):
    from app.repositories.models.conversation import MessageModel

    return MessageModel(
        role="assistant",
        content=[TextContentModel(content_type="text", body=text)],
        model=model_name,
        children=[],
        parent=None,
        create_time=get_current_time() / 1000.0,
    )

"""Model definitions for Claude Teams plan (OAuth token, via Claude Code CLI).

These models bypass Amazon Bedrock entirely: chat requests for them are
executed through the Claude Code CLI (via `claude-agent-sdk`), authenticated
with a `CLAUDE_CODE_OAUTH_TOKEN` drawn from a pool of organization-registered
tokens (see `app/claude_teams/token_pool.py`). Usage is billed against the
flat-rate Claude Teams/Pro/Max plan quota, not Bedrock on-demand pricing.
"""

from app.routes.schemas.conversation import type_model_name

# bedrock-chat model name -> Anthropic API native `model` value.
# These match the corresponding Bedrock BASE_MODEL_IDS entries in
# app/bedrock.py with the "anthropic." prefix stripped, except claude-haiku
# which pins the same snapshot as claude-v4.5-haiku.
CLAUDE_TEAMS_MODEL_IDS: dict[str, str] = {
    "claude-teams-opus": "claude-opus-5",
    "claude-teams-sonnet": "claude-sonnet-5",
    "claude-teams-haiku": "claude-haiku-4-5-20251001",
    "claude-teams-fable": "claude-fable-5",
}


def is_claude_teams_model(model: str) -> bool:
    """Whether `model` should be routed through the Claude Code CLI /
    Claude Teams OAuth token pool instead of Amazon Bedrock."""
    return model in CLAUDE_TEAMS_MODEL_IDS


def get_claude_teams_native_model_id(model: type_model_name | str) -> str:
    """Return the Anthropic API native `model` value for a `claude-teams-*`
    model name.

    Raises:
        ValueError: if `model` is not a known Claude Teams model name.
    """
    native_id = CLAUDE_TEAMS_MODEL_IDS.get(model)
    if native_id is None:
        raise ValueError(f"Not a Claude Teams model: {model}")
    return native_id

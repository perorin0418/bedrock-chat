"""Error classification for Claude Code CLI / claude-agent-sdk failures,
driving the token pool's cooldown/disable/passthrough behavior.

See `system/api_retry` event `error` field values documented at
https://code.claude.com/docs/en/headless#handle-api-retries.
"""

from typing import Literal

type_error_action = Literal["cooldown", "disable", "passthrough"]

_COOLDOWN_ERRORS = {"rate_limit", "billing_error"}
_DISABLE_ERRORS = {"authentication_failed", "oauth_org_not_allowed"}


class ClaudeTeamsAllTokensUnavailableError(Exception):
    """Raised when no Claude Teams OAuth token is currently available
    (all are disabled or cooling down)."""


class ClaudeTeamsExecutionError(Exception):
    """Raised for a Claude Code CLI failure that should be surfaced to the
    user as-is for this turn, without cooldown/disable or fallback."""


def classify_api_retry_error(error_kind: str) -> type_error_action:
    """Classify a `system/api_retry` event's `error` field into one of:

    - "cooldown": the token's flat-rate quota is exhausted for now; put it
      in cooldown and try the next token.
    - "disable": the token itself is invalid/revoked; disable it
      permanently and try the next token.
    - "passthrough": some other failure (overload, bad request, etc); do
      not touch the token, surface the error for this turn only.
    """
    if error_kind in _COOLDOWN_ERRORS:
        return "cooldown"
    if error_kind in _DISABLE_ERRORS:
        return "disable"
    return "passthrough"

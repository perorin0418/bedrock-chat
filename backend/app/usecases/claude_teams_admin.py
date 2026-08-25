"""Admin usecases for managing the Claude Teams OAuth token pool."""

from app.claude_teams.token_repository import (
    ClaudeTeamsTokenItem,
    create_token,
    delete_token,
    list_tokens,
    set_enabled,
)
from app.claude_teams.token_secrets import (
    delete_claude_teams_token,
    store_claude_teams_token,
)


def create_claude_teams_token(display_name: str, token_value: str) -> ClaudeTeamsTokenItem:
    """Register a new Claude Teams OAuth token: store the metadata row
    first (to get a token_id), then the secret."""
    item = create_token(display_name=display_name)
    store_claude_teams_token(item.token_id, token_value)
    return item


def list_claude_teams_tokens() -> list[ClaudeTeamsTokenItem]:
    return list_tokens()


def update_claude_teams_token(
    token_id: str, enabled: bool | None, display_name: str | None
) -> None:
    """Update mutable fields on a token. Only `enabled` is currently
    supported for update (display_name rename and token-string rotation
    are out of scope — see architecture doc: token string updates are
    delete + re-register)."""
    if enabled is not None:
        set_enabled(token_id, enabled)


def delete_claude_teams_token_usecase(token_id: str) -> None:
    """Delete both the DynamoDB metadata row and the Secrets Manager entry."""
    delete_token(token_id)
    delete_claude_teams_token(token_id)

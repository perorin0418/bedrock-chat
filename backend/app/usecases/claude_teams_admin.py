"""Admin usecases for managing the Claude Teams OAuth token pool."""

import csv
import io

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
from app.claude_teams.usage_history_repository import (
    ClaudeTeamsUsageHistoryItem,
    get_latest_usage_snapshot,
    list_usage_history,
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


def get_claude_teams_token_latest_usage(
    token_id: str,
) -> ClaudeTeamsUsageHistoryItem | None:
    """Return the most recently sampled usage-limit / token-validity
    snapshot for `token_id`, or None if the hourly sync hasn't run for
    this token yet."""
    return get_latest_usage_snapshot(token_id)


def build_claude_teams_usage_history_csv(
    since_ms: int, until_ms: int, token_id: str | None = None
) -> str:
    """Render usage-history snapshots within [since_ms, until_ms] as CSV
    text (UTF-8, header row included). If `token_id` is given, only that
    token's snapshots are included; otherwise every registered token's
    snapshots are included (one row per token per hourly sample), sorted
    by token then time."""
    all_tokens = list_tokens()
    display_names = {token.token_id: token.display_name for token in all_tokens}
    if token_id is not None:
        token_ids = [token_id]
    else:
        token_ids = list(display_names.keys())

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "token_id",
            "display_name",
            "sampled_at_ms",
            "fetch_status",
            "fetch_error_message",
            "five_hour_utilization",
            "five_hour_resets_at",
            "seven_day_utilization",
            "seven_day_resets_at",
        ]
    )
    for tid in token_ids:
        for item in list_usage_history(tid, since_ms, until_ms):
            writer.writerow(
                [
                    item.token_id,
                    display_names.get(tid, ""),
                    item.sampled_at_ms,
                    item.fetch_status,
                    item.fetch_error_message or "",
                    item.five_hour_utilization
                    if item.five_hour_utilization is not None
                    else "",
                    item.five_hour_resets_at or "",
                    item.seven_day_utilization
                    if item.seven_day_utilization is not None
                    else "",
                    item.seven_day_resets_at or "",
                ]
            )
    return buffer.getvalue()

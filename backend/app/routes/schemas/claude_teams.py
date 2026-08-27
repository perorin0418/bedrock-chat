from app.routes.schemas.base import BaseSchema


class ClaudeTeamsUsageSnapshotOutput(BaseSchema):
    sampled_at: int
    fetch_status: str
    fetch_error_message: str | None
    five_hour_utilization: float | None
    five_hour_resets_at: str | None
    seven_day_utilization: float | None
    seven_day_resets_at: str | None
    # True only when fetch_status == "auth_error": the OAuth token itself
    # is expired/revoked ("期限切れ"). Unrelated to usage-limit utilization,
    # which is a separate, self-resetting value.
    is_token_expired: bool


class ClaudeTeamsTokenOutput(BaseSchema):
    token_id: str
    display_name: str
    enabled: bool
    is_cooling_down: bool
    created_at: int
    last_used_at: int | None
    # Latest hourly-sampled usage-limit / token-validity snapshot, if any
    # has been recorded yet (None until the first sync cycle after
    # registration). See usage_history_repository for field semantics.
    latest_usage: ClaudeTeamsUsageSnapshotOutput | None = None


class CreateClaudeTeamsTokenInput(BaseSchema):
    display_name: str
    token_value: str


class UpdateClaudeTeamsTokenInput(BaseSchema):
    enabled: bool | None = None

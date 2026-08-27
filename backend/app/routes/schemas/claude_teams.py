from pydantic import field_validator

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

    @field_validator("token_value")
    @classmethod
    def strip_token_value(cls, value: str) -> str:
        """Strip all whitespace (leading/trailing/embedded) from the pasted
        OAuth token. Anthropic's `sk-ant-oat...` tokens never legitimately
        contain whitespace, but copy/paste from a terminal or browser can
        silently introduce a stray space or line break in the middle of the
        string (e.g. from word-wrapping), producing a token string that
        looks right at a glance but fails every API call with
        `authentication_error: OAuth access token is invalid.`"""
        return "".join(value.split())


class UpdateClaudeTeamsTokenInput(BaseSchema):
    enabled: bool | None = None

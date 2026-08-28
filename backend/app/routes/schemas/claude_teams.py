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


class ClaudeTeamsTokenCreateOutput(ClaudeTeamsTokenOutput):
    # Only present in the response to POST /admin/claude-teams-tokens
    # (registration time). Never returned again afterward — the admin
    # must copy it into the member's usage-reporting script config now.
    ingest_secret: str


class ClaudeTeamsUsageSnapshotIngestInput(BaseSchema):
    """Body for POST /claude-teams-tokens/{token_id}/usage-snapshot, sent
    by a member's local combined agent script (see
    scripts/claude_teams_member_agent.ps1) rather than the bedrock-chat
    frontend. Not Cognito-authenticated — see the route for why
    `ingest_secret` fills that role instead."""

    ingest_secret: str
    fetch_status: str
    fetch_error_message: str | None = None
    five_hour_utilization: float | None = None
    five_hour_resets_at: str | None = None
    seven_day_utilization: float | None = None
    seven_day_resets_at: str | None = None
    # Epoch milliseconds when the member's script actually queried
    # Anthropic's usage endpoint. Optional: the server stamps its own
    # receipt time if omitted, but a client-supplied value keeps the
    # history accurate under retry/network delay.
    sampled_at_ms: int | None = None


class ClaudeTeamsSelfRegisterInput(BaseSchema):
    """Body for POST /claude-teams-tokens/register, sent by a member's
    local combined agent script (see
    scripts/claude_teams_member_agent.ps1) on its first run to
    self-register a new pool token. Not Cognito-authenticated -- the
    org-wide `registration_secret` fills that role instead (see the
    route and docs/CLAUDE_TEAMS_OAUTH.md for the trust model)."""

    registration_secret: str
    display_name: str
    token_value: str

    @field_validator("token_value")
    @classmethod
    def strip_token_value(cls, value: str) -> str:
        # Same rationale as CreateClaudeTeamsTokenInput.strip_token_value.
        return "".join(value.split())


class ClaudeTeamsSelfRegisterOutput(BaseSchema):
    """Everything the member's script needs to save locally so future
    runs skip self-registration and go straight to pushing usage
    snapshots."""

    token_id: str
    ingest_secret: str


class ClaudeTeamsRegistrationSecretOutput(BaseSchema):
    registration_secret: str


class ClaudeTeamsTokenStatusOutput(BaseSchema):
    """Response for GET /claude-teams-tokens/{token_id}/status. Only a
    boolean, deliberately -- never the token string itself. See the
    route/usecase docstrings for what this is for."""

    enabled: bool

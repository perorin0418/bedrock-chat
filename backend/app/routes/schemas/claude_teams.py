from app.routes.schemas.base import BaseSchema


class ClaudeTeamsTokenOutput(BaseSchema):
    token_id: str
    display_name: str
    enabled: bool
    is_cooling_down: bool
    created_at: int
    last_used_at: int | None


class CreateClaudeTeamsTokenInput(BaseSchema):
    display_name: str
    token_value: str


class UpdateClaudeTeamsTokenInput(BaseSchema):
    enabled: bool | None = None

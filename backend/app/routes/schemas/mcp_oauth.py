from app.routes.schemas.base import BaseSchema


class McpOauthAuthorizeOutput(BaseSchema):
    authorization_url: str

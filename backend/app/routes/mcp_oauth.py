import logging

from app.routes.schemas.mcp_oauth import McpOauthAuthorizeOutput
from app.usecases.mcp_oauth import start_mcp_oauth_authorize
from app.user import User
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

router = APIRouter(tags=["mcp_oauth"])


@router.post(
    "/bot/{bot_id}/mcp-servers/{label}/oauth/authorize",
    response_model=McpOauthAuthorizeOutput,
)
async def post_mcp_oauth_authorize(request: Request, bot_id: str, label: str):
    """Start the OAuth authorization-code flow for one MCP server config."""
    current_user: User = request.state.current_user
    authorization_url = await start_mcp_oauth_authorize(current_user, bot_id, label)
    return McpOauthAuthorizeOutput(authorization_url=authorization_url)

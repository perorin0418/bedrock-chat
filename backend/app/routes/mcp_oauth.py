import logging
import os

from app.routes.schemas.mcp_oauth import McpOauthAuthorizeOutput
from app.usecases.mcp_oauth import (
    complete_mcp_oauth_callback,
    start_mcp_oauth_authorize,
)
from app.user import User
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

router = APIRouter(tags=["mcp_oauth"])

FRONTEND_URL = os.environ.get("FRONTEND_URL", "")


@router.post(
    "/bot/{bot_id}/mcp-servers/{label}/oauth/authorize",
    response_model=McpOauthAuthorizeOutput,
)
async def post_mcp_oauth_authorize(request: Request, bot_id: str, label: str):
    """Start the OAuth authorization-code flow for one MCP server config."""
    current_user: User = request.state.current_user
    authorization_url = await start_mcp_oauth_authorize(current_user, bot_id, label)
    return McpOauthAuthorizeOutput(authorization_url=authorization_url)


@router.get("/mcp/oauth/callback")
async def get_mcp_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str = "",
    error: str | None = None,
):
    """Atlassian (or any MCP OAuth server) redirects the user's browser here
    directly after consent. No Cognito auth: identity is established purely
    by the single-use `state` token (see CDK: this path is excluded from the
    Cognito authorizer)."""
    try:
        bot_id, label, succeeded = await complete_mcp_oauth_callback(
            code=code, state=state, error=error
        )
    except ValueError:
        logger.exception("MCP OAuth callback failed: unknown/expired state")
        return RedirectResponse(f"{FRONTEND_URL}/bot/new?mcpOauth=error")

    status = "success" if succeeded else "error"
    return RedirectResponse(
        f"{FRONTEND_URL}/bot/edit/{bot_id}?mcpOauth={status}&label={label}"
    )

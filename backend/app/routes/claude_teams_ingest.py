"""Self-registration and usage-snapshot ingest routes for the Claude
Teams OAuth token pool.

Both routes here are deliberately separate, un-Cognito-authenticated
routes (like `/mcp/oauth/callback` in `mcp_oauth.py` -- see CDK: these
paths are excluded from the Cognito authorizer) because the caller is
not a bedrock-chat frontend session but a member's own machine, running
`scripts/claude_teams_member_agent.ps1` on a schedule. That single
script:

  1. on its first run, reads the member's own `claude setup-token`
     accessToken from their local Claude Code CLI config and
     self-registers it as a new pool token via `register` below,
     saving the returned `token_id`/`ingest_secret` to a local config
     file next to the script,
  2. on every run (including that first one), calls Anthropic's
     undocumented `/api/oauth/usage` endpoint directly from the
     member's machine, and
  3. pushes only the resulting 5h/7d numbers to `usage-snapshot` below.

bedrock-chat never receives or stores the member's OAuth
accessToken/refreshToken in this flow -- only the resulting usage
percentages and (once, at self-registration) the chat-side OAuth token
string the admin pool needs to route chat requests. This means
bedrock-chat can never race the member's own local Claude Code CLI over
who gets to hold the "current" refresh token (see the architecture
discussion in docs/CLAUDE_TEAMS_OAUTH.md's "Usage-limit tracking"
section for why that mattered).

Authentication is two different per-purpose bearer secrets, both
verified in the usecase layer with a constant-time comparison:

  - `registration_secret`: one value shared by the whole org (see
    ClaudeTeamsRegistrationSecretOutput), authorizing "add a new pool
    token" only.
  - `ingest_secret`: one value per pool token (see
    ClaudeTeamsTokenCreateOutput / ClaudeTeamsSelfRegisterOutput),
    authorizing, for this one token_id only: pushing a usage snapshot,
    reading whether its chat-side token is still enabled, and asking
    which agent version to run (which also yields a short-lived
    presigned URL to download it -- see
    app/claude_teams/agent_release_repository.py for why that download
    must be gated at all).
"""

import logging

from app.routes.schemas.claude_teams import (
    ClaudeTeamsAgentReleaseInput,
    ClaudeTeamsAgentReleaseOutput,
    ClaudeTeamsSelfRegisterInput,
    ClaudeTeamsSelfRegisterOutput,
    ClaudeTeamsTokenStatusOutput,
    ClaudeTeamsUsageSnapshotIngestInput,
)
from app.usecases.claude_teams_admin import (
    AgentReleaseUnavailableError,
    InvalidIngestSecretError,
    InvalidRegistrationSecretError,
    get_agent_release,
    get_claude_teams_token_status,
    ingest_claude_teams_usage_snapshot,
    self_register_claude_teams_token,
)
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

router = APIRouter(tags=["claude_teams_ingest"])


@router.post("/claude-teams-tokens/register", response_model=ClaudeTeamsSelfRegisterOutput)
def post_claude_teams_self_register(body: ClaudeTeamsSelfRegisterInput):
    """Self-register a new Claude Teams pool token from a member's own
    machine. See module docstring for why this route is not
    Cognito-authenticated."""
    try:
        token = self_register_claude_teams_token(
            registration_secret=body.registration_secret,
            display_name=body.display_name,
            token_value=body.token_value,
        )
    except InvalidRegistrationSecretError:
        logger.warning("Rejected Claude Teams self-registration: bad registration secret")
        raise HTTPException(status_code=401, detail="Invalid registration secret.")

    assert token.ingest_secret is not None, "create_token must always mint an ingest_secret"
    return ClaudeTeamsSelfRegisterOutput(
        token_id=token.token_id, ingest_secret=token.ingest_secret
    )


@router.post("/claude-teams-tokens/{token_id}/usage-snapshot", status_code=204)
def post_claude_teams_usage_snapshot(
    token_id: str,
    body: ClaudeTeamsUsageSnapshotIngestInput,
):
    """Record one usage-limit snapshot pushed by a member's local agent
    script. See module docstring for why this route is not
    Cognito-authenticated."""
    try:
        ingest_claude_teams_usage_snapshot(
            token_id=token_id,
            ingest_secret=body.ingest_secret,
            fetch_status=body.fetch_status,
            fetch_error_message=body.fetch_error_message,
            five_hour_utilization=body.five_hour_utilization,
            five_hour_resets_at=body.five_hour_resets_at,
            seven_day_utilization=body.seven_day_utilization,
            seven_day_resets_at=body.seven_day_resets_at,
            sampled_at_ms=body.sampled_at_ms,
        )
    except InvalidIngestSecretError:
        logger.warning(f"Rejected usage-snapshot ingest for token_id={token_id}: bad secret")
        raise HTTPException(status_code=401, detail="Invalid token_id or ingest_secret.")


@router.get("/claude-teams-tokens/{token_id}/status", response_model=ClaudeTeamsTokenStatusOutput)
def get_claude_teams_token_status_route(token_id: str, ingest_secret: str):
    """Report whether this token's chat-side OAuth token is still
    enabled server-side (see module docstring). Returns only a boolean
    -- never the token string itself. Used by
    scripts/claude_teams_member_agent.ps1 to notice a token it
    registered was disabled (e.g. Anthropic rejected it as expired) and
    prompt the member to mint and register a fresh one."""
    try:
        enabled = get_claude_teams_token_status(
            token_id=token_id, ingest_secret=ingest_secret
        )
    except InvalidIngestSecretError:
        logger.warning(f"Rejected token-status check for token_id={token_id}: bad secret")
        raise HTTPException(status_code=401, detail="Invalid token_id or ingest_secret.")
    return ClaudeTeamsTokenStatusOutput(enabled=enabled)


@router.post(
    "/claude-teams-tokens/{token_id}/agent-version",
    response_model=ClaudeTeamsAgentReleaseOutput,
)
def post_claude_teams_agent_version(token_id: str, body: ClaudeTeamsAgentReleaseInput):
    """Report the currently published claude_teams_member_agent.exe
    version, with a short-lived presigned download URL, so an already
    installed agent can self-update on its next scheduled run instead of
    waiting for an admin to hand every member a rebuilt .exe by hand.

    Authenticated by the same per-token `ingest_secret` as the two routes
    above (see module docstring), which is what keeps the release binary
    -- and therefore the org-wide Registration Secret baked into it --
    from being downloadable by anyone who merely learns the URL.

    404 means "this deployment publishes no agent release" (no bucket, no
    manifest, or an incomplete one), which is a normal, non-error state
    for a deployment where the admin still distributes updates manually.
    The agent treats it as a no-op."""
    try:
        release = get_agent_release(token_id=token_id, ingest_secret=body.ingest_secret)
    except InvalidIngestSecretError:
        logger.warning(
            f"Rejected agent-version check for token_id={token_id}: bad secret"
        )
        raise HTTPException(
            status_code=401, detail="Invalid token_id or ingest_secret."
        )
    except AgentReleaseUnavailableError as e:
        logger.info(f"No agent release available for token_id={token_id}: {e}")
        raise HTTPException(status_code=404, detail="No agent release is published.")
    return ClaudeTeamsAgentReleaseOutput(
        version=release["version"],
        sha256=release["sha256"],
        download_url=release["download_url"],
    )

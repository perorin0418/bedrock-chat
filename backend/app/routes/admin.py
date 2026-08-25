from datetime import date

from app.dependencies import check_admin
from app.repositories.custom_bot import find_all_published_bots, find_bot_by_id
from app.repositories.usage_analysis import (
    find_bots_sorted_by_price,
    find_users_sorted_by_price,
)
from app.routes.schemas.admin import (
    PublicBotOutput,
    PublishedBotOutput,
    PublishedBotOutputsWithNextToken,
    PushBotInput,
    UsagePerBotOutput,
    UsagePerUserOutput,
)
from app.routes.schemas.bot import Knowledge
from app.routes.schemas.claude_teams import (
    ClaudeTeamsTokenOutput,
    CreateClaudeTeamsTokenInput,
    UpdateClaudeTeamsTokenInput,
)
from app.usecases.bot import modify_pinning_status
from app.usecases.claude_teams_admin import (
    create_claude_teams_token,
    delete_claude_teams_token_usecase,
    list_claude_teams_tokens,
    update_claude_teams_token,
)
from app.usecases.user import approve_pending_user, list_users_pending_approval
from app.user import User, UserWithoutGroups
from app.utils import get_current_time
from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter(tags=["admin"])


@router.get("/admin/published-bots", response_model=PublishedBotOutputsWithNextToken)
def get_all_published_bots(
    next_token: str | None = None,
    limit: int = 1000,
    admin_check=Depends(check_admin),
):
    """Get all published bots. This is intended to be used by admin."""
    bots, next_token = find_all_published_bots(next_token=next_token, limit=limit)

    bot_outputs = [
        PublishedBotOutput(
            id=bot.id,
            title=bot.title,
            description=bot.description,
            published_stack_name=bot.published_api_stack_name,
            published_datetime=bot.published_api_datetime,
            owner_user_id=bot.owner_user_id,
            shared_scope=bot.shared_scope,
            shared_status=bot.shared_status,
        )
        for bot in bots
    ]

    return PublishedBotOutputsWithNextToken(bots=bot_outputs, next_token=next_token)


@router.get("/admin/public-bots", response_model=list[UsagePerBotOutput])
async def get_all_public_bots(
    limit: int = 100,
    start: str | None = None,
    end: str | None = None,
    admin_check=Depends(check_admin),
):
    """Get all public bots. This is intended to be used by admin.
    NOTE:
    - limit: must be lower than 1000.
    - start: start date of the period to be analyzed. The format is `YYYYMMDDHH`.
    - end: end date of the period to be analyzed. The format is `YYYYMMDDHH`.
    - If start and end are not specified, start is set to today's 00:00 and end is set to 23:00.
    - The result is sorted by the total price in descending order.
    """
    bots = await find_bots_sorted_by_price(limit=limit, from_=start, to_=end)

    return [
        UsagePerBotOutput(
            id=bot.id,
            title=bot.title,
            description=bot.description,
            is_published=True if bot.published_api_stack_name else False,
            published_datetime=bot.published_api_datetime,
            shared_scope=bot.shared_scope,
            shared_status=bot.shared_status,
            owner_user_id=bot.owner_user_id,
            total_price=bot.total_price,
        )
        for bot in bots
    ]


@router.get("/admin/users", response_model=list[UsagePerUserOutput])
async def get_users(
    limit: int = 100,
    start: str | None = None,
    end: str | None = None,
    admin_check=Depends(check_admin),
):
    """Get all users. This is intended to be used by admin.
    NOTE:
    - limit: must be lower than 1000.
    - start: start date of the period to be analyzed. The format is `YYYYMMDDHH`.
    - end: end date of the period to be analyzed. The format is `YYYYMMDDHH`.
    - If start and end are not specified, start is set to today's 00:00 and end is set to 23:00.
    - The result is sorted by the total price in descending order.
    """
    users = await find_users_sorted_by_price(limit=limit, from_=start, to_=end)

    return [
        UsagePerUserOutput(
            id=user.id,
            email=user.email,
            total_price=user.total_price,
        )
        for user in users
    ]


@router.get("/admin/bot/public/{bot_id}", response_model=PublicBotOutput)
def get_public_bot(request: Request, bot_id: str, admin_check=Depends(check_admin)):
    """Get public (shared) bot by id."""
    bot = find_bot_by_id(bot_id)  # Note that permission check is done in `check_admin`.
    output = PublicBotOutput(
        id=bot.id,
        title=bot.title,
        instruction=bot.instruction,
        description=bot.description,
        create_time=bot.create_time,
        last_used_time=bot.last_used_time or bot.create_time,
        owner_user_id=bot.owner_user_id,
        knowledge=Knowledge(
            source_urls=bot.knowledge.source_urls,
            sitemap_urls=bot.knowledge.sitemap_urls,
            filenames=bot.knowledge.filenames,
            s3_urls=bot.knowledge.s3_urls,
        ),
        sync_status=bot.sync_status,
        sync_status_reason=bot.sync_status_reason,
        sync_last_exec_id=bot.sync_last_exec_id,
        shared_scope=bot.shared_scope,
        shared_status=bot.shared_status,
        allowed_cognito_groups=bot.allowed_cognito_groups,
        allowed_cognito_users=bot.allowed_cognito_users,
    )
    return output


@router.patch("/admin/bot/{bot_id}/pushed")
def pin_bot(
    request: Request,
    bot_id: str,
    push_input: PushBotInput,
    admin_check=Depends(check_admin),
):
    """Push / Un-push the bot."""
    modify_pinning_status(bot_id, push_input)


@router.get("/admin/users/pending", response_model=list[UserWithoutGroups])
def get_pending_users(
    limit: int = 60,
    admin_check=Depends(check_admin),
):
    """Get users who signed up but are still pending admin approval."""
    return list_users_pending_approval(limit=limit)


@router.patch("/admin/users/{user_id}/approve")
def approve_user(
    user_id: str,
    admin_check=Depends(check_admin),
):
    """Approve a pending user, allowing them to sign in."""
    approved = approve_pending_user(user_id)
    if not approved:
        raise HTTPException(status_code=404, detail="User Not Found.")


def _to_claude_teams_token_output(token) -> ClaudeTeamsTokenOutput:
    now_s = get_current_time() // 1000
    return ClaudeTeamsTokenOutput(
        token_id=token.token_id,
        display_name=token.display_name,
        enabled=token.enabled,
        is_cooling_down=token.cooldown_until is not None and token.cooldown_until > now_s,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
    )


@router.post("/admin/claude-teams-tokens", response_model=ClaudeTeamsTokenOutput)
def create_claude_teams_token_route(
    body: CreateClaudeTeamsTokenInput,
    admin_check=Depends(check_admin),
):
    """Register a new Claude Teams OAuth token. The token string is written
    to Secrets Manager and never returned again by any endpoint."""
    token = create_claude_teams_token(
        display_name=body.display_name, token_value=body.token_value
    )
    return _to_claude_teams_token_output(token)


@router.get("/admin/claude-teams-tokens", response_model=list[ClaudeTeamsTokenOutput])
def list_claude_teams_tokens_route(admin_check=Depends(check_admin)):
    """List registered Claude Teams OAuth tokens. The token string itself
    is never included in the response."""
    tokens = list_claude_teams_tokens()
    return [_to_claude_teams_token_output(token) for token in tokens]


@router.patch("/admin/claude-teams-tokens/{token_id}")
def update_claude_teams_token_route(
    token_id: str,
    body: UpdateClaudeTeamsTokenInput,
    admin_check=Depends(check_admin),
):
    """Enable or disable a token. Renaming and token-string rotation are
    not supported here (delete + re-register instead)."""
    update_claude_teams_token(
        token_id=token_id, enabled=body.enabled, display_name=None
    )


@router.delete("/admin/claude-teams-tokens/{token_id}")
def delete_claude_teams_token_route(
    token_id: str,
    admin_check=Depends(check_admin),
):
    """Permanently remove a token from the pool and delete its secret."""
    delete_claude_teams_token_usecase(token_id)

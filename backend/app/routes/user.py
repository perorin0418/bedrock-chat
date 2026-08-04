from venv import logger

from app.routes.schemas.rate_limit import UsageStatusOutput, UsageWindowOutput
from app.usecases.rate_limit import get_usage_status
from app.usecases.user import (
    get_user_by_id,
    search_group_by_name_prefix,
    search_user_by_email_prefix,
)
from app.user import User, UserGroup, UserWithoutGroups
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["user"])


@router.get("/user/usage-status", response_model=UsageStatusOutput)
def get_current_user_usage_status(request: Request) -> UsageStatusOutput:
    """Get the current user's recorded cost and configured limit for the
    trailing 5-hour and trailing 7-day rate-limit windows."""
    current_user: User = request.state.current_user

    status = get_usage_status(current_user.id)
    return UsageStatusOutput(
        five_hour=UsageWindowOutput(
            used=status.five_hour.used, limit=status.five_hour.limit
        ),
        seven_day=UsageWindowOutput(
            used=status.seven_day.used, limit=status.seven_day.limit
        ),
    )


@router.get("/user/search", response_model=list[UserWithoutGroups])
def search_user(request: Request, prefix: str):
    """Search users"""
    current_user: User = request.state.current_user
    logger.info("!!!")

    if not current_user.is_creating_bot_allowed():
        raise PermissionError("Search user is not allowed for the current user")

    users = search_user_by_email_prefix(prefix=prefix)
    return users


@router.get("/user/group/search", response_model=list[UserGroup])
def search_user_group(request: Request, prefix: str):
    """Search user groups"""
    current_user: User = request.state.current_user

    if not current_user.is_creating_bot_allowed():
        raise PermissionError("Search user group is not allowed for the current user")

    groups = search_group_by_name_prefix(prefix=prefix)
    return groups


@router.get("/user/{user_id}", response_model=UserWithoutGroups)
def get_user(request: Request, user_id: str):
    """Get user"""
    user = get_user_by_id(id=user_id)

    if user is None:
        raise HTTPException(status_code=404, detail="User Not Found.")

    return user

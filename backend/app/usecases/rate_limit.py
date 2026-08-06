import logging
import os
import time
from typing import NamedTuple

import boto3
from app.repositories.common import RateLimitExceededError
from app.repositories.usage_limit import get_usage_since
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

FIVE_HOUR_PARAM_NAME = os.environ.get("RATE_LIMIT_FIVE_HOUR_PARAM_NAME", "")
SEVEN_DAY_PARAM_NAME = os.environ.get("RATE_LIMIT_SEVEN_DAY_PARAM_NAME", "")

FIVE_HOUR_WINDOW_MS = 5 * 60 * 60 * 1000
SEVEN_DAY_WINDOW_MS = 7 * 24 * 60 * 60 * 1000

CACHE_TTL_SECONDS = 60

NO_RATE_LIMIT_GROUP_NAME = "NoRateLimit"
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")

ssm_client = boto3.client("ssm")
cognito_client = boto3.client("cognito-idp")

_limit_cache: dict[str, tuple[float, float]] = {}
_no_rate_limit_group_cache: dict[str, tuple[bool, float]] = {}


def _get_limit(param_name: str) -> float:
    cached = _limit_cache.get(param_name)
    now = time.time()
    if cached is not None and now - cached[1] < CACHE_TTL_SECONDS:
        return cached[0]

    try:
        response = ssm_client.get_parameter(Name=param_name)
        value = float(response["Parameter"]["Value"])
        _limit_cache[param_name] = (value, now)
        return value
    except Exception:
        if cached is not None:
            logger.warning(
                f"Failed to refresh SSM parameter {param_name}; using stale cached value."
            )
            return cached[0]
        raise


class UsageWindow(NamedTuple):
    used: float
    limit: float


class UsageStatus(NamedTuple):
    five_hour: UsageWindow
    seven_day: UsageWindow


def get_usage_status(user_id: str) -> UsageStatus:
    """Return `user_id`'s recorded cost and configured USD limit (from SSM)
    for both the trailing 5-hour and trailing 7-day windows."""
    now_ms = get_current_time()

    five_hour_limit = _get_limit(FIVE_HOUR_PARAM_NAME)
    five_hour_sum = get_usage_since(user_id, now_ms - FIVE_HOUR_WINDOW_MS)

    seven_day_limit = _get_limit(SEVEN_DAY_PARAM_NAME)
    seven_day_sum = get_usage_since(user_id, now_ms - SEVEN_DAY_WINDOW_MS)

    return UsageStatus(
        five_hour=UsageWindow(used=five_hour_sum, limit=five_hour_limit),
        seven_day=UsageWindow(used=seven_day_sum, limit=seven_day_limit),
    )


def _is_rate_limit_exempt(user_id: str) -> bool:
    """Return True if `user_id` belongs to the `NoRateLimit` Cognito group,
    in which case rate limiting should be skipped entirely."""
    if not USER_POOL_ID:
        return False

    cached = _no_rate_limit_group_cache.get(user_id)
    now = time.time()
    if cached is not None and now - cached[1] < CACHE_TTL_SECONDS:
        return cached[0]

    try:
        response = cognito_client.admin_list_groups_for_user(
            UserPoolId=USER_POOL_ID, Username=user_id
        )
        groups = [group["GroupName"] for group in response.get("Groups", [])]
        exempt = NO_RATE_LIMIT_GROUP_NAME in groups
    except Exception:
        logger.warning(
            f"Failed to look up Cognito groups for {user_id}; "
            "treating as not rate-limit exempt."
        )
        exempt = cached[0] if cached is not None else False

    _no_rate_limit_group_cache[user_id] = (exempt, now)
    return exempt


def check_rate_limit(user_id: str) -> None:
    """Raise `RateLimitExceededError` if `user_id`'s recorded cost exceeds
    either the trailing 5-hour or trailing 7-day USD limit (values read from
    SSM). Skipped entirely for users in the `NoRateLimit` Cognito group."""
    if _is_rate_limit_exempt(user_id):
        return

    status = get_usage_status(user_id)

    if status.five_hour.used > status.five_hour.limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded: ${status.five_hour.used:.2f} spent in the last 5 hours "
            f"(limit ${status.five_hour.limit:.2f})."
        )

    if status.seven_day.used > status.seven_day.limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded: ${status.seven_day.used:.2f} spent in the last 7 days "
            f"(limit ${status.seven_day.limit:.2f})."
        )

import logging
import os
import time

import boto3
from app.repositories.common import RateLimitExceededError
from app.repositories.usage_limit import get_usage_since
from app.user import User
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

FIVE_HOUR_PARAM_NAME = os.environ.get("RATE_LIMIT_FIVE_HOUR_PARAM_NAME", "")
SEVEN_DAY_PARAM_NAME = os.environ.get("RATE_LIMIT_SEVEN_DAY_PARAM_NAME", "")

FIVE_HOUR_WINDOW_MS = 5 * 60 * 60 * 1000
SEVEN_DAY_WINDOW_MS = 7 * 24 * 60 * 60 * 1000

CACHE_TTL_SECONDS = 60

ssm_client = boto3.client("ssm")

_limit_cache: dict[str, tuple[float, float]] = {}


def _get_limit(param_name: str) -> float:
    cached = _limit_cache.get(param_name)
    now = time.time()
    if cached is not None and now - cached[1] < CACHE_TTL_SECONDS:
        return cached[0]

    response = ssm_client.get_parameter(Name=param_name)
    value = float(response["Parameter"]["Value"])
    _limit_cache[param_name] = (value, now)
    return value


def check_rate_limit(user: User) -> None:
    """Raise `RateLimitExceededError` if `user`'s recorded cost exceeds either
    the trailing 5-hour or trailing 7-day USD limit (values read from SSM)."""
    now_ms = get_current_time()

    five_hour_limit = _get_limit(FIVE_HOUR_PARAM_NAME)
    five_hour_sum = get_usage_since(user.id, now_ms - FIVE_HOUR_WINDOW_MS)
    if five_hour_sum > five_hour_limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded: ${five_hour_sum:.2f} spent in the last 5 hours "
            f"(limit ${five_hour_limit:.2f})."
        )

    seven_day_limit = _get_limit(SEVEN_DAY_PARAM_NAME)
    seven_day_sum = get_usage_since(user.id, now_ms - SEVEN_DAY_WINDOW_MS)
    if seven_day_sum > seven_day_limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded: ${seven_day_sum:.2f} spent in the last 7 days "
            f"(limit ${seven_day_limit:.2f})."
        )

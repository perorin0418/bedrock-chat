"""Hourly Lambda: samples the Claude Teams OAuth token pool's 5-hour /
7-day usage-limit status from Anthropic's undocumented
`/api/oauth/usage` endpoint and appends one snapshot per token to
`ClaudeTeamsUsageHistoryTable`.

This is a standalone Lambda module (same pattern as
`claude_code_cost_sync/index.py`), not part of the `app` package, so it
has no dependency beyond boto3 and stdlib.

Endpoint reverse-engineered from the Claude Code / claude.ai clients (not
part of the documented public API — see
https://platform.claude.com/docs/en/api/rate-limits for the *documented*
per-request rate-limit headers, which this Lambda does not use since they
require making a real inference call). Response shape:

    {
      "five_hour": {"utilization": 12.0, "resets_at": "2026-05-14T19:40:00Z"},
      "seven_day": {"utilization": 2.0, "resets_at": "2026-05-21T16:00:01Z"},
      ...
    }

Two independent, unrelated things are recorded per snapshot — they must
not be conflated:

  - Usage-limit consumption (`five_hour_utilization` /
    `seven_day_utilization`, 0-100%): a normal, temporary state that
    resets automatically at `resets_at`. Reaching 100% does not mean the
    token is invalid.
  - Token validity (`fetch_status`): "ok" if the endpoint accepted the
    token, "auth_error" if it responded 401 (the OAuth token itself has
    expired or been revoked — this is the "期限切れ" state the admin
    screen should surface), or "error" for any other transport/API
    failure (e.g. a transient 5xx — not evidence the token is invalid).
"""

import json
import os
import time
import urllib.error
import urllib.request
from decimal import Decimal

import boto3

CLAUDE_TEAMS_USAGE_SYNC_ROLE_ARN = os.environ["CLAUDE_TEAMS_USAGE_SYNC_ROLE_ARN"]
CLAUDE_TEAMS_TOKEN_TABLE_NAME = os.environ["CLAUDE_TEAMS_TOKEN_TABLE_NAME"]
CLAUDE_TEAMS_USAGE_HISTORY_TABLE_NAME = os.environ[
    "CLAUDE_TEAMS_USAGE_HISTORY_TABLE_NAME"
]

USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_VERSION = "2023-06-01"
OAUTH_BETA_HEADER = "oauth-2025-04-20"
REQUEST_TIMEOUT_SECONDS = 15

# How long a usage-history snapshot is kept before DynamoDB TTL-prunes it.
# 90 days comfortably covers any CSV export range a reasonable admin would
# request while keeping the table small.
USAGE_HISTORY_TTL_SECONDS = 90 * 24 * 60 * 60

# This Lambda's own execution role (granted Secrets Manager read directly,
# since token secrets aren't row-scoped per bedrock-chat user).
secretsmanager = boto3.client("secretsmanager")
sts = boto3.client("sts")


def handler(event, context):
    session = _assume_usage_sync_role()
    token_table = session.resource("dynamodb").Table(CLAUDE_TEAMS_TOKEN_TABLE_NAME)
    usage_history_table = session.resource("dynamodb").Table(
        CLAUDE_TEAMS_USAGE_HISTORY_TABLE_NAME
    )

    tokens = _scan_tokens(token_table)
    now_ms = _now_ms()

    for token in tokens:
        token_id = token.get("TokenId")
        if not token_id:
            continue
        try:
            snapshot = _fetch_usage_snapshot(token_id)
        except Exception as e:
            # Isolate failures per token: one token's fetch error must not
            # block sampling everyone else in this cycle.
            print(f"Failed to sample usage for token '{token_id}': {e}")
            snapshot = _error_snapshot(str(e))
        _write_usage_history_item(usage_history_table, token_id, now_ms, snapshot)


def _assume_usage_sync_role() -> boto3.Session:
    assumed = sts.assume_role(
        RoleArn=CLAUDE_TEAMS_USAGE_SYNC_ROLE_ARN,
        RoleSessionName="ClaudeTeamsUsageSync",
    )
    credentials = assumed["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _scan_tokens(token_table) -> list[dict]:
    items: list[dict] = []
    response = token_table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = token_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return items


def _secret_name(token_id: str) -> str:
    return f"claude-teams-token/{token_id}"


def _get_token_value(token_id: str) -> str:
    response = secretsmanager.get_secret_value(SecretId=_secret_name(token_id))
    return response["SecretString"]


def _fetch_usage_snapshot(token_id: str) -> dict:
    """Call Anthropic's `/api/oauth/usage` with the token and return a
    snapshot dict. Never raises for an expected 401 (auth_error) —
    only for setup/transport failures the caller should log and
    fall back to `_error_snapshot` for."""
    oauth_token = _get_token_value(token_id)

    request = urllib.request.Request(
        USAGE_API_URL,
        headers={
            "Authorization": f"Bearer {oauth_token}",
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "anthropic-beta": OAUTH_BETA_HEADER,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {
                "fetch_status": "auth_error",
                "fetch_error_message": "401 Unauthorized (token expired or revoked)",
            }
        return {
            "fetch_status": "error",
            "fetch_error_message": f"HTTP {e.code}: {e.reason}",
        }

    return _parse_usage_body(body)


def _parse_usage_body(body: dict) -> dict:
    five_hour = body.get("five_hour") or {}
    seven_day = body.get("seven_day") or {}
    return {
        "fetch_status": "ok",
        "five_hour_utilization": five_hour.get("utilization"),
        "five_hour_resets_at": five_hour.get("resets_at"),
        "seven_day_utilization": seven_day.get("utilization"),
        "seven_day_resets_at": seven_day.get("resets_at"),
    }


def _error_snapshot(error_message: str) -> dict:
    return {"fetch_status": "error", "fetch_error_message": error_message}


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _write_usage_history_item(
    usage_history_table, token_id: str, now_ms: int, snapshot: dict
) -> None:
    item = {
        "TokenId": token_id,
        "SampledAtMs": now_ms,
        "FetchStatus": snapshot.get("fetch_status", "error"),
        "expire": now_ms // 1000 + USAGE_HISTORY_TTL_SECONDS,
    }
    if snapshot.get("fetch_error_message") is not None:
        item["FetchErrorMessage"] = snapshot["fetch_error_message"]

    five_hour_utilization = _to_decimal(snapshot.get("five_hour_utilization"))
    if five_hour_utilization is not None:
        item["FiveHourUtilization"] = five_hour_utilization
    if snapshot.get("five_hour_resets_at") is not None:
        item["FiveHourResetsAt"] = snapshot["five_hour_resets_at"]

    seven_day_utilization = _to_decimal(snapshot.get("seven_day_utilization"))
    if seven_day_utilization is not None:
        item["SevenDayUtilization"] = seven_day_utilization
    if snapshot.get("seven_day_resets_at") is not None:
        item["SevenDayResetsAt"] = snapshot["seven_day_resets_at"]

    usage_history_table.put_item(Item=item)

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

CLAUDE_CODE_COST_SYNC_ROLE_ARN = os.environ["CLAUDE_CODE_COST_SYNC_ROLE_ARN"]
USAGE_LEDGER_TABLE_NAME = os.environ["USAGE_LEDGER_TABLE_NAME"]
CLAUDE_CODE_IAM_USER_TABLE_NAME = os.environ["CLAUDE_CODE_IAM_USER_TABLE_NAME"]
GLUE_DATABASE_NAME = os.environ["GLUE_DATABASE_NAME"]
ATHENA_WORKGROUP = os.environ["ATHENA_WORKGROUP"]
RATE_LIMIT_FIVE_HOUR_PARAM_NAME = os.environ.get("RATE_LIMIT_FIVE_HOUR_PARAM_NAME", "")
RATE_LIMIT_SEVEN_DAY_PARAM_NAME = os.environ.get("RATE_LIMIT_SEVEN_DAY_PARAM_NAME", "")
CLAUDE_CODE_NOTIFICATION_TOPIC_ARN = os.environ.get(
    "CLAUDE_CODE_NOTIFICATION_TOPIC_ARN", ""
)

# Mirrors backend/app/usecases/rate_limit.py and
# backend/app/repositories/usage_limit.py. This Lambda is intentionally
# standalone (no dependency on the `app` package, matching the existing
# s3_exporter/add_user_to_groups Lambdas), so these constants and the
# get_usage_since/_get_limit logic below must be kept in sync manually if
# the originals change.
FIVE_HOUR_WINDOW_MS = 5 * 60 * 60 * 1000
SEVEN_DAY_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
USAGE_LEDGER_TTL_SECONDS = 8 * 24 * 60 * 60
MAX_QUERY_COUNT = 5
CACHE_TTL_SECONDS = 60

# 7-day rate-limit window plus a 1-day buffer for CUR data restatement.
SYNC_LOOKBACK_MS = 8 * 24 * 60 * 60 * 1000

IAM_USER_PRINCIPAL_PREFIX = "claude-code-"
DENY_POLICY_NAME = "ClaudeCodeRateLimitDeny"
DENY_POLICY_DOCUMENT = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ClaudeCodeRateLimitDeny",
            "Effect": "Deny",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:Converse",
                "bedrock:ConverseStream",
            ],
            "Resource": "*",
        }
    ],
}

ATHENA_POLL_INTERVAL_SECONDS = 2
ATHENA_MAX_POLL_ATTEMPTS = 60  # ~2 minutes

# These use this Lambda's own execution role (granted Athena/Glue/S3 access
# directly, since those resources live in this same stack).
sts = boto3.client("sts")
athena = boto3.client("athena")
glue = boto3.client("glue")
ssm = boto3.client("ssm")
sns = boto3.client("sns")

_limit_cache: dict[str, tuple[float, float]] = {}


def handler(event, context):
    session = _assume_cost_sync_role()
    usage_ledger_table = session.resource("dynamodb").Table(USAGE_LEDGER_TABLE_NAME)
    claude_code_iam_user_table = session.resource("dynamodb").Table(
        CLAUDE_CODE_IAM_USER_TABLE_NAME
    )
    iam_client = session.client("iam")

    try:
        table_name = _find_cur_table_name(GLUE_DATABASE_NAME)
        query = _build_cost_query(GLUE_DATABASE_NAME, table_name, SYNC_LOOKBACK_MS)
        rows = _run_athena_query(query, ATHENA_WORKGROUP)
        ledger_items = _rows_to_ledger_items(rows)
        _write_ledger_items(usage_ledger_table, ledger_items)
    except Exception as e:
        # Fail-open: if this cycle's query fails, leave the ledger and Deny
        # state untouched rather than guessing in either direction. The next
        # scheduled run retries automatically.
        print(
            "Claude Code cost sync query failed; skipping this cycle "
            "(ledger and Deny state left unchanged)"
        )
        _notify(
            "Claude Code cost sync failed",
            f"Athena query/ledger sync failed: {e}. See CloudWatch Logs for details.",
        )
        raise

    _evaluate_and_apply_deny(claude_code_iam_user_table, usage_ledger_table, iam_client)


def _assume_cost_sync_role() -> boto3.Session:
    assumed = sts.assume_role(
        RoleArn=CLAUDE_CODE_COST_SYNC_ROLE_ARN,
        RoleSessionName="ClaudeCodeCostSync",
    )
    credentials = assumed["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _notify(subject: str, message: str) -> None:
    """Best-effort SNS notification; must never raise."""
    if not CLAUDE_CODE_NOTIFICATION_TOPIC_ARN:
        return
    try:
        sns.publish(
            TopicArn=CLAUDE_CODE_NOTIFICATION_TOPIC_ARN,
            Subject=subject,
            Message=message,
        )
    except Exception:
        print("Failed to publish Claude Code cost-sync notification")


def _find_cur_table_name(database_name: str) -> str:
    """The Glue crawler names the CUR table dynamically, so discover it
    rather than assuming a fixed name. Raises if nothing has been crawled
    yet (e.g. shortly after first deploy, before CUR data has landed)."""
    response = glue.get_tables(DatabaseName=database_name)
    tables = response.get("TableList", [])
    if not tables:
        raise RuntimeError(
            f"No tables found in Glue database '{database_name}' yet "
            "(CUR data may not have been delivered/crawled yet)"
        )
    return tables[0]["Name"]


def _build_cost_query(database_name: str, table_name: str, lookback_ms: int) -> str:
    since_iso = _epoch_ms_to_iso(_now_ms() - lookback_ms)
    return (
        "SELECT line_item_iam_principal AS principal_arn, "
        "date_trunc('day', line_item_usage_start_date) AS usage_day, "
        "SUM(line_item_unblended_cost) AS total_cost "
        f'FROM "{database_name}"."{table_name}" '
        "WHERE line_item_line_item_type = 'Usage' "
        f"AND line_item_iam_principal LIKE '%:user/{IAM_USER_PRINCIPAL_PREFIX}%' "
        f"AND line_item_usage_start_date >= TIMESTAMP '{since_iso}' "
        "GROUP BY line_item_iam_principal, date_trunc('day', line_item_usage_start_date)"
    )


def _epoch_ms_to_iso(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _run_athena_query(query: str, workgroup: str) -> list[dict]:
    execution = athena.start_query_execution(QueryString=query, WorkGroup=workgroup)
    query_execution_id = execution["QueryExecutionId"]

    for _ in range(ATHENA_MAX_POLL_ATTEMPTS):
        state = athena.get_query_execution(QueryExecutionId=query_execution_id)[
            "QueryExecution"
        ]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Athena query {state.lower()}: {query_execution_id}")
        time.sleep(ATHENA_POLL_INTERVAL_SECONDS)
    else:
        raise TimeoutError(f"Athena query timed out: {query_execution_id}")

    return _paginate_query_results(query_execution_id)


def _paginate_query_results(query_execution_id: str) -> list[dict]:
    rows: list[dict] = []
    header: list[str] | None = None
    next_token = None
    while True:
        kwargs = {"QueryExecutionId": query_execution_id}
        if next_token:
            kwargs["NextToken"] = next_token
        response = athena.get_query_results(**kwargs)
        for row in response["ResultSet"]["Rows"]:
            values = [field.get("VarCharValue") for field in row["Data"]]
            if header is None:
                header = values
                continue
            rows.append(dict(zip(header, values)))
        next_token = response.get("NextToken")
        if not next_token:
            break
    return rows


def _user_id_from_principal_arn(principal_arn: str) -> str | None:
    marker = f"user/{IAM_USER_PRINCIPAL_PREFIX}"
    idx = principal_arn.find(marker)
    if idx == -1:
        return None
    return principal_arn[idx + len(marker) :]


def _parse_athena_timestamp_to_epoch_ms(value: str) -> int:
    # Athena returns TIMESTAMP columns as "YYYY-MM-DD HH:MM:SS[.ffffff]".
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized Athena timestamp format: {value}")


def _rows_to_ledger_items(rows: list[dict]) -> list[dict]:
    items = []
    for row in rows:
        principal_arn = row.get("principal_arn")
        usage_day = row.get("usage_day")
        total_cost = row.get("total_cost")
        if not principal_arn or not usage_day or total_cost is None:
            continue
        user_id = _user_id_from_principal_arn(principal_arn)
        if user_id is None:
            continue
        items.append(
            {
                "user_id": user_id,
                "day_start_ms": _parse_athena_timestamp_to_epoch_ms(usage_day),
                "total_cost": Decimal(total_cost),
            }
        )
    return items


def _write_ledger_items(usage_ledger_table, items: list[dict]) -> None:
    for item in items:
        usage_ledger_table.put_item(
            Item={
                "PK": item["user_id"],
                "SK": item["day_start_ms"],
                "Price": item["total_cost"],
                "expire": item["day_start_ms"] // 1000 + USAGE_LEDGER_TTL_SECONDS,
                # Observability marker only; not used by aggregation logic.
                "Source": "claude_code_cur_sync",
            }
        )


def get_usage_since(usage_ledger_table, user_id: str, since_ms: int) -> float:
    """Mirrors app.repositories.usage_limit.get_usage_since exactly."""
    total = Decimal("0")
    key_condition = Key("PK").eq(user_id) & Key("SK").gte(since_ms)
    last_evaluated_key = None
    for _ in range(MAX_QUERY_COUNT):
        kwargs = {"KeyConditionExpression": key_condition}
        if last_evaluated_key:
            kwargs["ExclusiveStartKey"] = last_evaluated_key
        response = usage_ledger_table.query(**kwargs)
        for item in response.get("Items", []):
            total += item["Price"]
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
    return float(total)


def _get_limit(param_name: str) -> float:
    """Mirrors app.usecases.rate_limit._get_limit's 60s in-memory cache and
    stale-cache fallback on SSM read failure."""
    now = time.time()
    cached = _limit_cache.get(param_name)
    if cached and now - cached[1] < CACHE_TTL_SECONDS:
        return cached[0]
    try:
        value = float(ssm.get_parameter(Name=param_name)["Parameter"]["Value"])
        _limit_cache[param_name] = (value, now)
        return value
    except Exception:
        if cached:
            return cached[0]
        raise


def _is_denied(iam_client, iam_user_name: str) -> bool:
    try:
        iam_client.get_user_policy(UserName=iam_user_name, PolicyName=DENY_POLICY_NAME)
        return True
    except iam_client.exceptions.NoSuchEntityException:
        return False


def _apply_deny(iam_client, iam_user_name: str) -> None:
    iam_client.put_user_policy(
        UserName=iam_user_name,
        PolicyName=DENY_POLICY_NAME,
        PolicyDocument=_deny_policy_document_json(),
    )


def _remove_deny(iam_client, iam_user_name: str) -> None:
    try:
        iam_client.delete_user_policy(
            UserName=iam_user_name, PolicyName=DENY_POLICY_NAME
        )
    except iam_client.exceptions.NoSuchEntityException:
        pass  # already removed; idempotent


def _deny_policy_document_json() -> str:
    return json.dumps(DENY_POLICY_DOCUMENT)


def _scan_provisioned_users(claude_code_iam_user_table) -> list[dict]:
    users = []
    last_evaluated_key = None
    while True:
        kwargs = {}
        if last_evaluated_key:
            kwargs["ExclusiveStartKey"] = last_evaluated_key
        response = claude_code_iam_user_table.scan(**kwargs)
        users.extend(response.get("Items", []))
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
    return users


def _evaluate_and_apply_deny(
    claude_code_iam_user_table, usage_ledger_table, iam_client
) -> None:
    now_ms = _now_ms()
    five_hour_limit = _get_limit(RATE_LIMIT_FIVE_HOUR_PARAM_NAME)
    seven_day_limit = _get_limit(RATE_LIMIT_SEVEN_DAY_PARAM_NAME)

    for provisioned_user in _scan_provisioned_users(claude_code_iam_user_table):
        user_id = provisioned_user.get("UserId")
        iam_user_name = provisioned_user.get("IamUserName")
        if not user_id or not iam_user_name:
            continue
        was_denied = bool(provisioned_user.get("DenyActive", False))
        try:
            five_hour_sum = get_usage_since(
                usage_ledger_table, user_id, now_ms - FIVE_HOUR_WINDOW_MS
            )
            seven_day_sum = get_usage_since(
                usage_ledger_table, user_id, now_ms - SEVEN_DAY_WINDOW_MS
            )
            exceeded = (
                five_hour_sum > five_hour_limit or seven_day_sum > seven_day_limit
            )

            if exceeded:
                _apply_deny(iam_client, iam_user_name)
                if not was_denied:
                    _notify(
                        "Claude Code Deny applied",
                        f"user_id={user_id} (iam_user_name={iam_user_name}) "
                        "exceeded the rate limit; Bedrock access has been denied.",
                    )
            else:
                _remove_deny(iam_client, iam_user_name)

            claude_code_iam_user_table.update_item(
                Key={"UserId": user_id},
                UpdateExpression="SET DenyActive = :deny_active, LastSyncTime = :now",
                ExpressionAttributeValues={":deny_active": exceeded, ":now": now_ms},
            )
        except Exception:
            # Isolate failures per user: one user's Deny-evaluation error
            # must not block evaluation of everyone else in this cycle.
            print(f"Failed to evaluate/apply Deny state for user '{user_id}'")
            continue

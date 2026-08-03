import os
import json
import time

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

USER_POOL_ID: str = os.environ["USER_POOL_ID"]
AUTO_JOIN_USER_GROUPS: list[str] = json.loads(
    os.environ.get("AUTO_JOIN_USER_GROUPS", "[]")
)
REQUIRE_ADMIN_APPROVAL: bool = (
    os.environ.get("REQUIRE_ADMIN_APPROVAL", "false").lower() == "true"
)
ENABLE_CLAUDE_CODE_PROVISIONING: bool = (
    os.environ.get("ENABLE_CLAUDE_CODE_PROVISIONING", "false").lower() == "true"
)
CLAUDE_CODE_IAM_USER_TABLE_NAME: str = os.environ.get(
    "CLAUDE_CODE_IAM_USER_TABLE_NAME", ""
)
CLAUDE_CODE_BEDROCK_POLICY_ARN: str = os.environ.get(
    "CLAUDE_CODE_BEDROCK_POLICY_ARN", ""
)
CLAUDE_CODE_NOTIFICATION_TOPIC_ARN: str = os.environ.get(
    "CLAUDE_CODE_NOTIFICATION_TOPIC_ARN", ""
)
CLAUDE_CODE_SECRET_PREFIX = "claude-code"

logger = Logger()
tracer = Tracer()

cognito = boto3.client("cognito-idp")
iam = boto3.client("iam")
secretsmanager = boto3.client("secretsmanager")
dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def handler(event: dict, context: LambdaContext) -> dict:
    user_name: str = event["userName"]
    user_attributes: dict = event["request"]["userAttributes"]

    trigger_source: str = event["triggerSource"]
    if trigger_source == "PostConfirmation_ConfirmSignUp":
        add_user_to_groups(USER_POOL_ID, user_name, AUTO_JOIN_USER_GROUPS)
        if REQUIRE_ADMIN_APPROVAL:
            disable_user_pending_approval(USER_POOL_ID, user_name)
        if ENABLE_CLAUDE_CODE_PROVISIONING:
            # user_id must match `sub`, the same Cognito identifier used as
            # user_id everywhere else in the app (e.g. usage_limit ledger),
            # not `userName` which can be an email depending on IdP config.
            provision_claude_code_iam_user(user_attributes["sub"], user_name)

    elif trigger_source == "PostAuthentication_Authentication":
        user_status: str = user_attributes["cognito:user_status"]
        if user_status == "FORCE_CHANGE_PASSWORD":
            add_user_to_groups(USER_POOL_ID, user_name, AUTO_JOIN_USER_GROUPS)

    return event


def add_user_to_groups(user_pool_id: str, username: str, groups: list[str]):
    for group in groups:
        logger.info(f"Adding user '{username}' to group '{group}'")
        cognito.admin_add_user_to_group(
            UserPoolId=user_pool_id,
            Username=username,
            GroupName=group,
        )


def disable_user_pending_approval(user_pool_id: str, username: str):
    logger.info(f"Disabling user '{username}' pending admin approval")
    cognito.admin_disable_user(
        UserPoolId=user_pool_id,
        Username=username,
    )


def _notify(subject: str, message: str) -> None:
    """Best-effort SNS notification; must never raise (always called from
    within a failure-handling path already)."""
    if not CLAUDE_CODE_NOTIFICATION_TOPIC_ARN:
        return
    try:
        sns.publish(
            TopicArn=CLAUDE_CODE_NOTIFICATION_TOPIC_ARN,
            Subject=subject,
            Message=message,
        )
    except Exception:
        logger.exception("Failed to publish Claude Code notification")


def _iam_user_name(user_id: str) -> str:
    return f"claude-code-{user_id}"


def _secret_name(user_id: str) -> str:
    return f"{CLAUDE_CODE_SECRET_PREFIX}/{user_id}"


def _ensure_iam_user(user_id: str) -> str:
    """Create the employee's Claude Code IAM user if it doesn't already
    exist, and make sure the Bedrock access policy is attached. Returns the
    IAM user ARN. Idempotent: safe to call again if this Lambda is retried.
    """
    iam_user_name = _iam_user_name(user_id)
    try:
        response = iam.create_user(UserName=iam_user_name)
        user_arn = response["User"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        response = iam.get_user(UserName=iam_user_name)
        user_arn = response["User"]["Arn"]

    iam.attach_user_policy(
        UserName=iam_user_name, PolicyArn=CLAUDE_CODE_BEDROCK_POLICY_ARN
    )
    return user_arn


def _ensure_access_key(user_id: str) -> dict | None:
    """Create a Bedrock access key for the employee's IAM user, unless an
    active key already exists (IAM allows at most 2 keys per user, so a
    retry must not create a third). Returns
    {"AccessKeyId": ..., "SecretAccessKey": ...} for a newly created key, or
    None if an active key already exists.
    """
    iam_user_name = _iam_user_name(user_id)
    existing_keys = iam.list_access_keys(UserName=iam_user_name)["AccessKeyMetadata"]
    if any(key["Status"] == "Active" for key in existing_keys):
        logger.info(f"Active access key already exists for '{iam_user_name}'")
        return None

    response = iam.create_access_key(UserName=iam_user_name)
    return {
        "AccessKeyId": response["AccessKey"]["AccessKeyId"],
        "SecretAccessKey": response["AccessKey"]["SecretAccessKey"],
    }


def _store_access_key_secret(
    user_id: str, access_key_id: str, secret_access_key: str
) -> str:
    """Store the access key in Secrets Manager. Returns the secret ARN."""
    secret_name = _secret_name(user_id)
    secret_value = json.dumps(
        {"AccessKeyId": access_key_id, "SecretAccessKey": secret_access_key}
    )
    try:
        response = secretsmanager.create_secret(
            Name=secret_name, SecretString=secret_value
        )
        return response["ARN"]
    except secretsmanager.exceptions.ResourceExistsException:
        response = secretsmanager.update_secret(
            SecretId=secret_name, SecretString=secret_value
        )
        return response["ARN"]


def _record_provisioning(
    user_id: str,
    iam_user_name: str,
    iam_user_arn: str,
    secret_arn: str,
    status: str,
) -> None:
    """Record-keeping only (provisioning status, for observability and for
    the cost-sync Lambda to enumerate provisioned users). Best-effort: must
    never block provisioning, unlike the IAM user/key/secret steps above.
    """
    try:
        table = dynamodb.Table(CLAUDE_CODE_IAM_USER_TABLE_NAME)
        table.put_item(
            Item={
                "UserId": user_id,
                "IamUserName": iam_user_name,
                "IamUserArn": iam_user_arn,
                "SecretArn": secret_arn,
                "ProvisionStatus": status,
                "DenyActive": False,
                "CreateTime": int(time.time() * 1000),
            }
        )
    except Exception:
        logger.exception(
            f"Failed to record Claude Code provisioning state for '{user_id}'"
        )


def provision_claude_code_iam_user(user_id: str, cognito_username: str) -> None:
    """Provision (idempotently) an IAM user, Bedrock access key, and Secrets
    Manager entry so this employee can use Claude Code directly against
    Bedrock, with usage tracked against the same rate-limit ledger as their
    Bedrock Chat usage.

    Never raises: by the time PostConfirmation runs, the Cognito sign-up
    itself has already succeeded, so a failure here must not block or
    reverse it. Failures are logged for CloudWatch-based alerting instead.
    """
    iam_user_name = _iam_user_name(user_id)
    try:
        iam_user_arn = _ensure_iam_user(user_id)
        new_key = _ensure_access_key(user_id)

        secret_arn = ""
        if new_key is not None:
            try:
                secret_arn = _store_access_key_secret(
                    user_id, new_key["AccessKeyId"], new_key["SecretAccessKey"]
                )
            except Exception:
                logger.exception(
                    f"Failed to store Claude Code access key secret for "
                    f"'{user_id}', rolling back the newly created access key"
                )
                iam.delete_access_key(
                    UserName=iam_user_name, AccessKeyId=new_key["AccessKeyId"]
                )
                raise

        _record_provisioning(user_id, iam_user_name, iam_user_arn, secret_arn, "ACTIVE")
    except Exception:
        logger.exception(
            f"Failed to provision Claude Code IAM user for "
            f"'{cognito_username}' (user_id={user_id})"
        )
        _notify(
            "Claude Code IAM provisioning failed",
            f"Provisioning failed for user_id={user_id}, "
            f"cognito_username={cognito_username}. See CloudWatch Logs for details.",
        )

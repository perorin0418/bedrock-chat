"""Secrets Manager storage for Claude Teams OAuth token strings, plus the
org-wide self-registration secret (see `get_or_create_registration_secret`
below).

Deterministic secret name `claude-teams-token/{token_id}` — the token_id
comes from the DynamoDB pool (see `token_repository.py`) and doubles as the
Secrets Manager key, so no ARN needs to be tracked separately.

The token string is stored as a raw `SecretString` (not JSON-wrapped),
unlike `store_api_key_to_secret_manager` in `app/utils.py`, since there is
exactly one value to store per secret and no per-bot/per-user namespacing
is needed here.
"""

import logging
import secrets

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# One fixed secret shared by the whole org, distributed once by an admin to
# every member who'll run scripts/claude_teams_member_agent.ps1. Lets a
# member's own machine self-register its `claude setup-token` value as a
# new Claude Teams pool token via the un-Cognito-authenticated
# POST /claude-teams-tokens/register route, without an admin having to
# manually paste each token into the web UI first. See
# docs/CLAUDE_TEAMS_OAUTH.md for the trust model this implies (anyone
# holding it can add -- not read, not remove -- pool tokens).
REGISTRATION_SECRET_NAME = "claude-teams-registration-secret"


def _secret_name(token_id: str) -> str:
    return f"claude-teams-token/{token_id}"


def store_claude_teams_token(token_id: str, token_value: str) -> None:
    """Create or update the Secrets Manager entry for one pool token."""
    secret_name = _secret_name(token_id)
    client = boto3.client("secretsmanager")

    try:
        client.describe_secret(SecretId=secret_name)
        client.update_secret(SecretId=secret_name, SecretString=token_value)
        logger.info(f"Updated existing secret for token {token_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            client.create_secret(Name=secret_name, SecretString=token_value)
            logger.info(f"Created new secret for token {token_id}")
        else:
            raise


def get_claude_teams_token(token_id: str) -> str:
    """Return the raw OAuth token string for `token_id`."""
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=_secret_name(token_id))
    return response["SecretString"]


def delete_claude_teams_token(token_id: str) -> None:
    """Permanently delete the Secrets Manager entry for `token_id`
    (no recovery window — the pool table row is deleted alongside this,
    and there is no reason to keep a de-registered OAuth token around)."""
    client = boto3.client("secretsmanager")
    client.delete_secret(SecretId=_secret_name(token_id), ForceDeleteWithoutRecovery=True)


def get_or_create_registration_secret() -> str:
    """Return the org-wide self-registration secret, minting one on first
    call (lazy creation: no CDK-time provisioning needed)."""
    client = boto3.client("secretsmanager")
    try:
        response = client.get_secret_value(SecretId=REGISTRATION_SECRET_NAME)
        return response["SecretString"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    new_secret = secrets.token_urlsafe(32)
    try:
        client.create_secret(Name=REGISTRATION_SECRET_NAME, SecretString=new_secret)
    except ClientError as e:
        # Lost a create race against a concurrent first call; read back
        # whatever the winner stored instead of erroring.
        if e.response["Error"]["Code"] == "ResourceExistsException":
            return client.get_secret_value(SecretId=REGISTRATION_SECRET_NAME)[
                "SecretString"
            ]
        raise
    return new_secret


def regenerate_registration_secret() -> str:
    """Rotate the org-wide self-registration secret. Any member's
    machine still holding the old value will get a 401 on its next
    self-registration attempt (already-registered tokens are unaffected
    — their per-token `ingest_secret` is independent of this)."""
    new_secret = secrets.token_urlsafe(32)
    client = boto3.client("secretsmanager")
    try:
        client.describe_secret(SecretId=REGISTRATION_SECRET_NAME)
        client.update_secret(SecretId=REGISTRATION_SECRET_NAME, SecretString=new_secret)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            client.create_secret(Name=REGISTRATION_SECRET_NAME, SecretString=new_secret)
        else:
            raise
    return new_secret

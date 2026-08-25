"""Secrets Manager storage for Claude Teams OAuth token strings.

Deterministic secret name `claude-teams-token/{token_id}` — the token_id
comes from the DynamoDB pool (see `token_repository.py`) and doubles as the
Secrets Manager key, so no ARN needs to be tracked separately.

The token string is stored as a raw `SecretString` (not JSON-wrapped),
unlike `store_api_key_to_secret_manager` in `app/utils.py`, since there is
exactly one value to store per secret and no per-bot/per-user namespacing
is needed here.
"""

import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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

"""Secrets Manager-backed TokenStorage for MCP OAuth (see mcp.client.auth.oauth2.TokenStorage).

Blob shape under Secrets Manager prefix "mcp-oauth" (one entry per bot,
shared across that bot's oauth-type MCP servers, keyed by server label):
    {"<label>": {"client_info": {...}, "tokens": {...}}}

The secret name is deterministic (`mcp-oauth/{user_id}/{bot_id}`), so no ARN
needs to be tracked/persisted anywhere: `get_api_key_from_secret_manager`'s
`SecretId` parameter accepts either a real ARN or a plain secret name.
"""

import asyncio
import json
import logging

from botocore.exceptions import ClientError

from app.utils import get_api_key_from_secret_manager, store_api_key_to_secret_manager
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SecretsManagerTokenStorage:
    """`mcp.client.auth.oauth2.TokenStorage` implementation. boto3 calls are
    synchronous, so each method offloads to a thread."""

    def __init__(self, user_id: str, bot_id: str, label: str) -> None:
        self.user_id = user_id
        self.bot_id = bot_id
        self.label = label

    @property
    def _secret_name(self) -> str:
        return f"mcp-oauth/{self.user_id}/{self.bot_id}"

    def _load_blob(self) -> dict[str, dict]:
        try:
            raw = get_api_key_from_secret_manager(self._secret_name)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.warning(
                    f"No existing MCP oauth secret for bot '{self.bot_id}', starting empty"
                )
                return {}
            raise
        return json.loads(raw) if raw else {}

    def _save_blob(self, blob: dict[str, dict]) -> None:
        store_api_key_to_secret_manager(
            self.user_id, self.bot_id, "mcp-oauth", json.dumps(blob)
        )

    async def get_tokens(self) -> OAuthToken | None:
        def _get() -> OAuthToken | None:
            entry = self._load_blob().get(self.label, {}).get("tokens")
            return OAuthToken.model_validate(entry) if entry else None

        return await asyncio.to_thread(_get)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        def _set() -> None:
            blob = self._load_blob()
            blob.setdefault(self.label, {})["tokens"] = tokens.model_dump(mode="json")
            self._save_blob(blob)

        await asyncio.to_thread(_set)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        def _get() -> OAuthClientInformationFull | None:
            entry = self._load_blob().get(self.label, {}).get("client_info")
            return OAuthClientInformationFull.model_validate(entry) if entry else None

        return await asyncio.to_thread(_get)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        def _set() -> None:
            blob = self._load_blob()
            blob.setdefault(self.label, {})["client_info"] = client_info.model_dump(
                mode="json"
            )
            self._save_blob(blob)

        await asyncio.to_thread(_set)

"""Discovery, Dynamic Client Registration, and token exchange for MCP OAuth.

Split into standalone request/response steps (rather than driving
`mcp.client.auth.oauth2.OAuthClientProvider`'s single continuous
`async_auth_flow` generator) because our authorize/callback endpoints are two
separate HTTP requests, with the user's browser round trip to Atlassian in
between -- there is no single async context to run the SDK's generator in.
Chat-time usage (`mcp_tools.py`) uses `OAuthClientProvider` directly instead,
since by then no interactive step is needed.
"""

import logging
from urllib.parse import urlencode

import httpx
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_registration_request,
    create_oauth_metadata_request,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

HTTP_TIMEOUT_SECONDS = 10


async def discover_oauth_metadata(server_url: str) -> OAuthMetadata:
    """Discover the MCP server's protected-resource metadata, then its
    authorization-server metadata. Does NOT perform Dynamic Client
    Registration -- safe to call repeatedly (e.g. on every OAuth callback)
    without registering a new (orphan) OAuth client each time."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        auth_server_url: str | None = None
        for url in build_protected_resource_metadata_discovery_urls(None, server_url):
            response = await client.send(create_oauth_metadata_request(url))
            prm = await handle_protected_resource_response(response)
            if prm:
                auth_server_url = str(prm.authorization_servers[0])
                break

        oauth_metadata: OAuthMetadata | None = None
        for url in build_oauth_authorization_server_metadata_discovery_urls(
            auth_server_url, server_url
        ):
            response = await client.send(create_oauth_metadata_request(url))
            ok, asm = await handle_auth_metadata_response(response)
            if not ok:
                break
            if asm:
                oauth_metadata = asm
                break

        if oauth_metadata is None:
            raise ValueError(
                f"Could not discover OAuth authorization server metadata for {server_url}"
            )

        return oauth_metadata


async def register_client(
    oauth_metadata: OAuthMetadata, server_url: str, redirect_uri: str
) -> OAuthClientInformationFull:
    """Register a new OAuth client via Dynamic Client Registration (RFC 7591).
    Call once per MCP server config; the returned `client_info` should be
    persisted (`SecretsManagerTokenStorage.set_client_info`) and reused for
    subsequent authorize attempts -- calling this again would register a new
    (orphan) client rather than reusing the existing registration."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        client_metadata = OAuthClientMetadata(
            redirect_uris=[redirect_uri],  # type: ignore[list-item]
            grant_types=["authorization_code", "refresh_token"],
            token_endpoint_auth_method="client_secret_post",
        )
        registration_request = create_client_registration_request(
            oauth_metadata, client_metadata, _authorization_base_url(server_url)
        )
        registration_response = await client.send(registration_request)
        return await handle_registration_response(registration_response)


async def discover_and_register(
    server_url: str, redirect_uri: str
) -> tuple[OAuthMetadata, OAuthClientInformationFull]:
    """Discover the MCP server's protected-resource/authorization-server
    metadata, then register a new OAuth client via Dynamic Client
    Registration (RFC 7591). Convenience wrapper combining
    `discover_oauth_metadata` and `register_client`; use those directly
    when only discovery is needed (e.g. in the OAuth callback, where DCR
    must not run a second time)."""
    oauth_metadata = await discover_oauth_metadata(server_url)
    client_info = await register_client(oauth_metadata, server_url, redirect_uri)
    return oauth_metadata, client_info


def build_authorization_url(
    oauth_metadata: OAuthMetadata,
    client_info: OAuthClientInformationFull,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scope: str | None,
) -> str:
    """Build the URL the bot owner's browser is redirected to for consent."""
    params = {
        "response_type": "code",
        "client_id": client_info.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    return f"{oauth_metadata.authorization_endpoint}?{urlencode(params)}"


async def exchange_code_for_tokens(
    oauth_metadata: OAuthMetadata,
    client_info: OAuthClientInformationFull,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> OAuthToken:
    """Exchange an authorization code (+ PKCE verifier) for tokens."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_info.client_id,
        "code_verifier": code_verifier,
    }
    if client_info.client_secret:
        data["client_secret"] = client_info.client_secret

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(str(oauth_metadata.token_endpoint), data=data)

    if response.status_code != 200:
        raise ValueError(
            f"MCP OAuth token exchange failed ({response.status_code}): {response.text}"
        )

    return OAuthToken.model_validate(response.json())


def _authorization_base_url(server_url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(server_url)
    return f"{parsed.scheme}://{parsed.netloc}"

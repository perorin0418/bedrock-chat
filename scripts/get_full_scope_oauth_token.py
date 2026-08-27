#!/usr/bin/env python3
"""Interactive helper: obtain a full-scope, long-lived Claude Code OAuth
token (including the `user:profile` scope needed by Anthropic's
`/api/oauth/usage` endpoint) and print it for registration in the
bedrock-chat "Claude Teams Tokens" admin page.

Background
----------
`claude setup-token` (Anthropic's official CLI command) only requests the
`user:inference` scope. That's sufficient for chat/inference calls but is
*not* sufficient for the `/api/oauth/usage` endpoint bedrock-chat's
`claude_teams_usage_sync` Lambda polls hourly to show 5-hour/7-day usage
limits and detect token expiry -- that endpoint requires `user:profile`
and returns `403 permission_error` without it.

This script drives the same Authorization Code + PKCE OAuth flow Claude
Code itself uses (`claude.ai/oauth/authorize` ->
`console.anthropic.com/v1/oauth/token`), but explicitly requests the
broader `org:create_api_key user:profile user:inference` scope set and an
extended `expires_in` lifetime, matching the pattern documented at
https://gist.github.com/ben-vargas/c7c7cbfebbb47278f45feca9cef309d1
(there is no first-party Anthropic documentation for this -- endpoint
details/scope behavior may change without notice).

This is an interactive, copy/paste flow: no local HTTP server or browser
automation required, so it works on a headless machine too.

Usage
-----
    python get_full_scope_oauth_token.py

Then follow the printed instructions: open the URL, authorize, and paste
the code back. The resulting access token is printed once at the end --
copy it directly into the admin page's "OAuth Token" field. It is never
written to disk by this script.
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

CLIENT_ID = os.environ.get(
    "ANTHROPIC_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
)
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"

# `user:profile` is the scope bedrock-chat's usage-history sync needs;
# `user:inference` is what chat/inference calls need (already covered by
# `claude setup-token`); `org:create_api_key` is included because it's part
# of the scope set Claude Code's own full-scope login requests, and asking
# for less than that set has been observed to make some servers reject the
# request outright.
SCOPES = "org:create_api_key user:profile user:inference"

# One year, matching Claude Code's own long-lived `setup-token` lifetime.
# The server may cap or ignore this and return a shorter validity.
DEFAULT_EXPIRES_IN_SECONDS = 31536000


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_pkce() -> tuple[str, str]:
    verifier = _base64url(secrets.token_bytes(32))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _build_authorize_url(challenge: str, state: str) -> str:
    from urllib.parse import urlencode

    params = {
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _exchange_code_for_token(
    code: str, state: str, verifier: str, expires_in: int
) -> dict:
    body = json.dumps(
        {
            "grant_type": "authorization_code",
            "code": code,
            "state": state,
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "expires_in": expires_in,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            # console.anthropic.com sits behind Cloudflare, which rejects
            # Python's default `Python-urllib/x.y` User-Agent (and the
            # TLS/HTTP fingerprint that goes with it) outright with a
            # generic "error code: 1010" (bad browser signature) — no
            # Anthropic-specific error body, just a Cloudflare block page.
            # A standard browser User-Agent is enough to pass; the request
            # itself doesn't need to (and isn't trying to) impersonate the
            # Claude Code CLI's own client identity.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Token exchange failed: HTTP {e.code}\n{error_body}"
        ) from e


def main() -> int:
    print(__doc__.split("Usage\n-----")[0].strip())
    print()
    print("=" * 72)

    verifier, challenge = _generate_pkce()
    state = verifier
    url = _build_authorize_url(challenge, state)

    print("Step 1: Open this URL in your browser and authorize:")
    print()
    print(url)
    print()
    print("Step 2: After authorizing, you'll see an authorization code.")
    print('If it looks like "CODE#STATE", paste that whole string below.')
    print("If it's just a plain code, paste that.")
    print()

    raw_input_value = input("Authorization code: ").strip()
    if not raw_input_value:
        print("No code entered; aborting.", file=sys.stderr)
        return 1

    if "#" in raw_input_value:
        code, returned_state = raw_input_value.split("#", 1)
    else:
        code, returned_state = raw_input_value, state

    print()
    print("Exchanging code for a full-scope OAuth token...")
    try:
        token_response = _exchange_code_for_token(
            code=code,
            state=returned_state,
            verifier=verifier,
            expires_in=DEFAULT_EXPIRES_IN_SECONDS,
        )
    except RuntimeError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1

    access_token = token_response.get("access_token")
    if not access_token:
        print(
            f"\nERROR: no access_token in response: {token_response}",
            file=sys.stderr,
        )
        return 1

    granted_scope = token_response.get("scope", "(not reported)")
    expires_in = token_response.get("expires_in", "(not reported)")

    print()
    print("=" * 72)
    print("SUCCESS. Full-scope OAuth token obtained:")
    print()
    print(access_token)
    print()
    print(f"Granted scope: {granted_scope}")
    print(f"expires_in (seconds, as reported by server): {expires_in}")
    print()
    print(
        "Copy the token above into bedrock-chat's admin \"Claude Teams "
        'Tokens" page, "OAuth Token" field. This script does not write it '
        "to any file."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

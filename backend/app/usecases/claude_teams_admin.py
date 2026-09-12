"""Admin usecases for managing the Claude Teams OAuth token pool."""

import csv
import hmac
import io

from app.claude_teams.token_repository import (
    ClaudeTeamsTokenItem,
    create_token,
    delete_token,
    get_token,
    list_tokens,
    set_enabled,
)
from app.claude_teams.agent_release_repository import (
    AgentReleaseNotConfiguredError,
    generate_agent_download_url,
    get_agent_release_manifest,
)
from app.claude_teams.token_secrets import (
    delete_claude_teams_token,
    get_or_create_registration_secret,
    regenerate_registration_secret,
    store_claude_teams_token,
)
from app.claude_teams.usage_history_repository import (
    ClaudeTeamsUsageHistoryItem,
    get_latest_usage_snapshot,
    list_usage_history,
    write_usage_snapshot,
)


def create_claude_teams_token(display_name: str, token_value: str) -> ClaudeTeamsTokenItem:
    """Register a new Claude Teams OAuth token: store the metadata row
    first (to get a token_id), then the secret."""
    item = create_token(display_name=display_name)
    store_claude_teams_token(item.token_id, token_value)
    return item


def list_claude_teams_tokens() -> list[ClaudeTeamsTokenItem]:
    return list_tokens()


def update_claude_teams_token(
    token_id: str, enabled: bool | None, display_name: str | None
) -> None:
    """Update mutable fields on a token. Only `enabled` is currently
    supported for update (display_name rename and token-string rotation
    are out of scope — see architecture doc: token string updates are
    delete + re-register)."""
    if enabled is not None:
        set_enabled(token_id, enabled)


def delete_claude_teams_token_usecase(token_id: str) -> None:
    """Delete both the DynamoDB metadata row and the Secrets Manager entry."""
    delete_token(token_id)
    delete_claude_teams_token(token_id)


def get_claude_teams_token_latest_usage(
    token_id: str,
) -> ClaudeTeamsUsageHistoryItem | None:
    """Return the most recently sampled usage-limit / token-validity
    snapshot for `token_id`, or None if the hourly sync hasn't run for
    this token yet."""
    return get_latest_usage_snapshot(token_id)


def build_claude_teams_usage_history_csv(
    since_ms: int, until_ms: int, token_id: str | None = None
) -> str:
    """Render usage-history snapshots within [since_ms, until_ms] as CSV
    text (UTF-8, header row included). If `token_id` is given, only that
    token's snapshots are included; otherwise every registered token's
    snapshots are included (one row per token per hourly sample), sorted
    by token then time."""
    all_tokens = list_tokens()
    display_names = {token.token_id: token.display_name for token in all_tokens}
    if token_id is not None:
        token_ids = [token_id]
    else:
        token_ids = list(display_names.keys())

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "token_id",
            "display_name",
            "sampled_at_ms",
            "fetch_status",
            "fetch_error_message",
            "five_hour_utilization",
            "five_hour_resets_at",
            "seven_day_utilization",
            "seven_day_resets_at",
        ]
    )
    for tid in token_ids:
        for item in list_usage_history(tid, since_ms, until_ms):
            writer.writerow(
                [
                    item.token_id,
                    display_names.get(tid, ""),
                    item.sampled_at_ms,
                    item.fetch_status,
                    item.fetch_error_message or "",
                    item.five_hour_utilization
                    if item.five_hour_utilization is not None
                    else "",
                    item.five_hour_resets_at or "",
                    item.seven_day_utilization
                    if item.seven_day_utilization is not None
                    else "",
                    item.seven_day_resets_at or "",
                ]
            )
    return buffer.getvalue()


class InvalidIngestSecretError(Exception):
    """Raised when a usage-snapshot push presents a wrong/missing ingest
    secret for the given token_id, or the token_id doesn't exist."""


def _verify_ingest_secret(token_id: str, ingest_secret: str) -> ClaudeTeamsTokenItem:
    """Return the pool token for `token_id` after confirming the presented
    per-token `ingest_secret` matches the one minted for it at registration
    time, raising InvalidIngestSecretError otherwise.

    Shared by every un-Cognito-authenticated member-machine route (usage
    snapshot push, chat-token status, agent-version check) so all three
    fail identically -- same exception, hence the same 401 with the same
    deliberately non-committal "Invalid token_id or ingest_secret" detail,
    which doesn't disclose whether a guessed token_id exists.

    Uses `hmac.compare_digest` to avoid a timing side-channel on the
    secret comparison, even though the token_id itself isn't secret."""
    token = get_token(token_id)
    if token is None or token.ingest_secret is None:
        raise InvalidIngestSecretError(f"Unknown token_id: {token_id}")
    if not hmac.compare_digest(token.ingest_secret, ingest_secret):
        raise InvalidIngestSecretError(
            f"Invalid ingest secret for token_id: {token_id}"
        )
    return token


def ingest_claude_teams_usage_snapshot(
    token_id: str,
    ingest_secret: str,
    fetch_status: str,
    fetch_error_message: str | None,
    five_hour_utilization: float | None,
    five_hour_resets_at: str | None,
    seven_day_utilization: float | None,
    seven_day_resets_at: str | None,
    sampled_at_ms: int | None,
) -> None:
    """Record one usage-limit snapshot pushed by a member's local
    usage-reporting script (see scripts/report_claude_teams_usage.ps1),
    after verifying the presented `ingest_secret` matches the one minted
    for `token_id` at registration time.

    This is the "member runs `claude setup-token` + a scheduled task"
    path: it doesn't touch the OAuth token itself (bedrock-chat never
    sees the member's accessToken/refreshToken here, only the resulting
    5h/7d numbers), so it carries none of the refresh_token-rotation
    conflict risk of bedrock-chat polling `/api/oauth/usage` directly
    with a token a member is also actively using locally.

    Uses `hmac.compare_digest` for the secret comparison to avoid a
    timing side-channel, even though the token_id itself isn't secret."""
    _verify_ingest_secret(token_id, ingest_secret)

    write_usage_snapshot(
        token_id=token_id,
        fetch_status=fetch_status,
        fetch_error_message=fetch_error_message,
        five_hour_utilization=five_hour_utilization,
        five_hour_resets_at=five_hour_resets_at,
        seven_day_utilization=seven_day_utilization,
        seven_day_resets_at=seven_day_resets_at,
        sampled_at_ms=sampled_at_ms,
    )


def get_claude_teams_token_status(token_id: str, ingest_secret: str) -> bool:
    """Return whether `token_id`'s chat-side OAuth token is still enabled
    (i.e. bedrock-chat hasn't permanently disabled it after an
    authentication_failed/oauth_org_not_allowed error from Anthropic --
    see disable_token / errors.py). Lets a member's own machine notice
    "the chat token I registered has been revoked/expired server-side,
    I should mint a new one" without ever exposing the token string
    itself over the network again.

    Verified with the same per-token `ingest_secret` as the
    usage-snapshot push (see ingest_claude_teams_usage_snapshot) --
    deliberately not a new secret, so there's nothing extra for a member
    to lose or an admin to regenerate/distribute for this to work.

    Raises InvalidIngestSecretError for an unknown token_id or a bad
    secret, exactly like ingest_claude_teams_usage_snapshot, so the
    route layer can map both to the same 401."""
    token = _verify_ingest_secret(token_id, ingest_secret)
    return token.enabled


class AgentReleaseUnavailableError(Exception):
    """Raised when this deployment publishes no agent release (no bucket
    configured, no manifest uploaded, or a manifest missing the fields an
    agent needs to update safely).

    Distinct from InvalidIngestSecretError so the route can answer 404
    ("nothing published") rather than 401 ("who are you") -- the agent
    treats the former as a routine no-op and the latter as a real
    problem."""


def get_agent_release(token_id: str, ingest_secret: str) -> dict:
    """Return the currently published claude_teams_member_agent.exe
    release for a member's already-registered machine to compare against
    its own baked-in version, plus a short-lived presigned download URL.

    Authenticated with the same per-token `ingest_secret` as the
    usage-snapshot push and chat-token status check -- deliberately not a
    new credential (nothing extra for a member to lose or an admin to
    distribute), and deliberately not the org-wide registration secret,
    which is itself baked into the very binary being served and so would
    be circular. The practical effect is that only machines already in
    the pool can download the .exe; see
    app/claude_teams/agent_release_repository.py for why that matters
    given the binary carries the Registration Secret.

    Returns a dict with `version`, `sha256` and `download_url`. The
    caller is *not* trusted to have checked anything: the agent verifies
    the downloaded bytes against `sha256` before installing them, so a
    compromised or misconfigured bucket object cannot be silently
    executed on a member's machine.

    Raises:
        InvalidIngestSecretError: unknown token_id or wrong secret.
        AgentReleaseUnavailableError: nothing published for this
            deployment, or the manifest is incomplete.
    """
    _verify_ingest_secret(token_id, ingest_secret)

    try:
        manifest = get_agent_release_manifest()
    except AgentReleaseNotConfiguredError as e:
        raise AgentReleaseUnavailableError(str(e))

    version = str(manifest.get("version") or "").strip()
    sha256 = str(manifest.get("sha256") or "").strip()
    key = str(manifest.get("key") or "").strip()
    # All three are mandatory, and a manifest missing any one of them is
    # treated as "nothing published" rather than partially honored: an
    # agent handed a version without a sha256 would have to choose
    # between refusing to update (silently stuck) or installing
    # unverified bytes (the exact risk this design exists to remove).
    # Failing closed here keeps that decision out of the agent entirely.
    if not version or not sha256 or not key:
        raise AgentReleaseUnavailableError(
            "Agent release manifest must define version, sha256 and key."
        )

    return {
        "version": version,
        "sha256": sha256,
        "download_url": generate_agent_download_url(key),
    }


class InvalidRegistrationSecretError(Exception):
    """Raised when a self-registration request presents a registration
    secret that doesn't match the org-wide value."""


def get_claude_teams_registration_secret() -> str:
    """Return the org-wide self-registration secret for the admin page to
    display (minting one on first call). See
    scripts/claude_teams_member_agent.ps1 and
    docs/CLAUDE_TEAMS_OAUTH.md for how members use it."""
    return get_or_create_registration_secret()


def regenerate_claude_teams_registration_secret() -> str:
    """Rotate the org-wide self-registration secret (e.g. it leaked
    outside the org). Already-registered tokens are unaffected."""
    return regenerate_registration_secret()


def self_register_claude_teams_token(
    registration_secret: str, display_name: str, token_value: str
) -> ClaudeTeamsTokenItem:
    """Register a new Claude Teams OAuth token from a member's own
    machine (see scripts/claude_teams_member_agent.ps1), after verifying
    the presented `registration_secret` matches the org-wide value.

    This is the self-service counterpart to
    `create_claude_teams_token` (the admin-page path): the member never
    needs an admin to manually paste their token into the web UI first.
    The registration secret only grants "can add a new pool token" --
    it cannot read, list, disable, or delete existing tokens, and each
    newly-created token still gets its own independent `ingest_secret`
    for the usage-snapshot push path.

    Uses `hmac.compare_digest` to avoid a timing side-channel on the
    secret comparison."""
    expected = get_or_create_registration_secret()
    if not hmac.compare_digest(expected, registration_secret):
        raise InvalidRegistrationSecretError("Invalid registration secret.")

    return create_claude_teams_token(display_name=display_name, token_value=token_value)

# Claude Teams OAuth Token Chat

## Overview

This feature lets Bedrock Chat users select dedicated "(Teams Plan)" model
variants (`claude-teams-opus`, `claude-teams-sonnet`, `claude-teams-haiku`,
`claude-teams-fable`) that route chat requests through the real Claude Code
CLI (via the `claude-agent-sdk` Python package) instead of Amazon Bedrock.
Requests are billed against a pool of organization-registered Claude
Teams/Pro/Max plan OAuth tokens (`CLAUDE_CODE_OAUTH_TOKEN`), consuming their
flat-rate quota rather than Bedrock on-demand pricing.

This is designed to avoid impersonating the Claude Code CLI (a documented
Anthropic ToS concern with directly forging its system prompt against the
plain Anthropic API): the actual Claude Code CLI binary, bundled inside the
`claude-agent-sdk` pip package as a native executable, is spawned as a
subprocess for every chat turn.

## Setup

1. As an Admin, go to the "Claude Teams Tokens" admin page and copy the
   org-wide **Registration Secret** (or call
   `GET /admin/claude-teams-tokens/registration-secret`; it's minted on
   first request). This one value is shared by every member -- it only
   grants "add a new pool token", nothing else (see "Known
   limitations").
2. Copy `scripts/claude_teams_member_agent.bat` and edit its two
   `set` lines near the top to your actual Registration Secret and
   bedrock-chat API endpoint URL (both are org-wide, identical for
   every member). Then distribute that already-edited `.bat` (together
   with `claude_teams_member_agent.ps1`, which it calls) to each
   Teams/Pro/Max seat holder willing to share their quota -- members
   should not need to edit anything themselves.
3. Each member runs the distributed script **exactly once** (just
   double-clicking the `.bat` you already filled in). That single run:
   - runs `claude setup-token` (always, even if the member is already
     logged into Claude Code some other way -- see "Why always run
     setup-token" below), which opens a browser for one-time approval
     and then prints a long-lived OAuth token to the terminal,
   - prompts the member to paste that printed token back into the
     script (masked input; the script itself does not read
     `%USERPROFILE%\.claude\.credentials.json` for this),
   - self-registers that pasted token with bedrock-chat via
     `POST /claude-teams-tokens/register`, saving the resulting
     per-token `token_id`/`ingest_secret` to a small local JSON file at
     a fixed per-user location,
     `%USERPROFILE%\.claude\claude_teams_member_agent.config.json`
     (deliberately not next to the script/.bat, so moving or
     re-distributing the script doesn't lose track of an already
     registered token and mint a duplicate one),
   - registers itself as a recurring Windows Task Scheduler entry
     (hourly by default, `-TaskIntervalMinutes` to change it) that
     re-runs the same script -- no manual Task Scheduler setup needed.
     Before registering the task, it also copies the `.ps1` (and
     `.bat`, if found alongside it) to that same fixed
     `%USERPROFILE%\.claude\claude_teams_member_agent\` folder and
     points the scheduled task at the copy, so the recurring task keeps
     working even if the admin-distributed folder the member originally
     ran it from is later moved, deleted, or was on removable/network
     media.
4. Users select a "(Teams Plan)" model from the model picker to route their
   chat through this pool.

Nothing further needs to be configured on the member's machine after
that one run -- see "Usage-limit tracking" below for what the recurring
scheduled runs do.

### Why always run setup-token

`claude setup-token` mints a token scoped to `user:inference` only and
prints it to the terminal; per Anthropic's own docs it is never written
to any file, including `.credentials.json`. `.credentials.json` is
instead written by the normal interactive `claude login`/`/login` flow,
and commonly carries a much broader scope (e.g. `user:profile`,
`org:create_api_key`, depending on the account/CLI version) than
`user:inference` alone.

An earlier version of this script skipped `claude setup-token` and
silently read `.credentials.json` directly whenever it already existed
(e.g. because the member had already run `claude login` for normal
day-to-day use). That could send bedrock-chat a broader-scoped
credential than the member realized, without any warning. The script
now always runs `claude setup-token` and has the member paste its
printed output, so the token actually registered is guaranteed to be
the narrow, inference-only one -- regardless of what's already sitting
in `.credentials.json` for the member's own separate CLI usage.

Note: an admin can still register a token manually from the "Claude
Teams Tokens" page (`POST /admin/claude-teams-tokens`) if a member can't
run the script themselves -- both paths write to the same pool and
produce an independent `ingest_secret` either way.

An earlier version of this doc recommended
`scripts/get_full_scope_oauth_token.bat` (now removed), a non-official
PKCE flow requesting the broader `org:create_api_key user:profile
user:inference` scope so bedrock-chat itself could poll
`/api/oauth/usage` server-side. As of mid-2026, Anthropic's
`/v1/oauth/token` token-exchange endpoint returns a persistent
`rate_limit_error` for this scope combination regardless of IP or
account (see e.g. anthropics/claude-code#38248, a similar report
Anthropic closed "not planned"), so that approach is no longer
reliable. `claude_teams_member_agent.ps1` only ever uses the plain
`claude setup-token` flow (`user:inference` only), which is unaffected.

## Usage-limit tracking

Anthropic's undocumented `/api/oauth/usage` endpoint reports 5-hour /
7-day rate-limit utilization for a given OAuth token, but requires the
`user:profile` scope that plain `claude setup-token` tokens don't carry
and that the broader-scope flow above can no longer reliably mint.
`scripts/claude_teams_member_agent.ps1` works around this using a
**separate** credential from the chat-side one: the member's own
`%USERPROFILE%\.claude\.credentials.json`, written by whatever normal
`claude login`/`/login` session the member uses day-to-day on that
machine (unrelated to the pasted `claude setup-token` output used for
chat -- see "Why always run setup-token" above). That file commonly
does carry `user:profile`, so it can query `/api/oauth/usage` directly.
The script pushes (not bedrock-chat polling) only the resulting
percentages:

- On every scheduled run (the recurring Task Scheduler entry the script
  registered for itself at first run -- see Setup), it reads
  `%USERPROFILE%\.claude\.credentials.json`, calls `/api/oauth/usage`
  from that machine, and posts the result to
  `POST /claude-teams-tokens/{token_id}/usage-snapshot` -- a
  deliberately un-Cognito-authenticated route (verified instead by that
  token's own `ingest_secret`) since the caller is the member's
  machine, not a bedrock-chat frontend session.
- This never sends the member's `.credentials.json` accessToken/
  refreshToken to bedrock-chat on these recurring runs -- only the
  resulting percentages -- so it carries none of the
  refresh_token-rotation conflict risk that would come from
  bedrock-chat holding and refreshing a token the member is also
  actively using locally (whichever side refreshes last invalidates
  the other's copy). Refreshing happens only on the member's own
  machine, and only when the token has already lapsed -- see
  "Refreshing the local login" below. The chat-side token (the narrow, inference-only
  one `claude setup-token` prints, pasted by the member) is sent to
  bedrock-chat exactly once, at first-run self-registration (see
  Setup) -- the same one-time transfer that pasting it into the admin
  web UI would have been.
- If the member's `.credentials.json` login doesn't carry
  `user:profile` (or the file doesn't exist), `/api/oauth/usage` calls
  from this script fail and it reports `fetch_status: "auth_error"` or
  `"error"` for the usage-tracking signal -- this does **not** mean the
  chat-side token (registered separately, from the pasted
  `setup-token` output) is invalid. This is reported to the admin page
  but, deliberately, **shows the member nothing** -- see below.

#### Refreshing the local login

The local Claude Code access token lapses within hours of the CLI not
being used, so without renewal a member who isn't actively using Claude
Code reports `auth_error` on nearly every scheduled run.

The agent therefore refreshes it, **but only once it has already
expired**. That condition is the whole safety argument:

- OAuth refresh tokens rotate: whoever refreshes last invalidates the
  other side's copy. Refreshing a *live* token could log the member out
  of the CLI they are working in at that moment. Breaking someone's real
  work to collect a usage statistic is plainly the wrong trade.
- An already-expired access token means the CLI is not currently in use
  (Claude Code renews it as it goes). There is no live session to
  disturb, the token is dead to both sides, and renewing it is exactly
  what the CLI itself would do on next launch.

Consequently an **unknown** expiry (no `expiresAt` in the file) is
treated as *not* expired: guessing would risk rotating a credential a
live CLI depends on, whereas being wrong the other way costs only a
single 401 that the existing path already handles.

The refresh uses the same public OAuth client the CLI uses
(`https://platform.claude.com/v1/oauth/token`, Claude Code's client ID,
JSON body), so the token obtained is the one the CLI would have got.
Implementation notes worth knowing:

- **The credentials file is rewritten by editing its raw JSON**, not by
  re-serializing the agent's own struct. It carries fields this agent
  does not model (`refreshTokenExpiresAt`, `scopes`,
  `subscriptionType`, `rateLimitTier`, `trustedDeviceToken`, and
  whatever Anthropic adds next); overwriting them would break the
  member's CLI far worse than the expired token being fixed. Written
  via temp-file + atomic rename at `0600`.
- **The rotated refresh token is stored**; keeping the spent one would
  make the next refresh fail.
- **Failure is never fatal.** A failed refresh falls back to the
  expired token, so the usual 401 → `auth_error` path runs exactly as
  it did before this existed.
- **Refresh errors carry a status code only**, never the response body,
  which on this endpoint can echo token material into logs and the
  stored `fetch_error_message`.

#### Why an expired local login is silent

The agent briefly showed a desktop dialog on the `auth_error` (401)
case, asking the member to run `claude login`. That was removed.

The local Claude Code login this reads expires within hours of not
using Claude Code, and the agent never refreshes it. So for any member
who simply isn't using Claude Code at the moment, a 401 here is the
normal, expected state rather than a fault -- and the dialog fired on
every scheduled run, nagging precisely the people with nothing to act
on. What it asked them to restore was worth little anyway: usage-limit
percentages for someone who, by definition, is not consuming usage.

The snapshot is still posted as `auth_error`, so the admin page
reflects reality, and the warning still goes to stderr for anyone
running the exe by hand. If the member does start using Claude Code
again, the next scheduled run picks the refreshed login up on its own.

With the refresh above in place, a 401 here is now much rarer -- it
means the refresh itself failed or was impossible (no refresh token,
refresh token expired, network blocked). Those are still nothing a
member can usefully act on from a dialog, so the reasoning is
unchanged.

Contrast with the chat-token case below, which keeps its dialog: that
one is rare rather than routine, invisible to the member by
construction, costly to leave broken (their seat serves no chat), and
fixable by a single double-click. This one is none of those.

### Chat-token expiry detection

The chat-side token (the `claude setup-token` output the member pasted at
first run) is held **server-side only** -- `setup-token` printed it once
and saved it nowhere, so the member's machine has no copy and cannot
check its expiry locally. But when that server-side copy stops working,
chat routing permanently disables the token (see "Behavior") and the
member's seat silently stops serving requests, with nothing on their
machine to tell them.

To close that gap, every scheduled run of the member script calls:

- `GET /claude-teams-tokens/{token_id}/status?ingest_secret=...` --
  authenticated by the same per-token `ingest_secret` (no new secret),
  likewise un-Cognito-authenticated, and returning **only**
  `{"enabled": bool}`. It never returns the token string; no API
  anywhere exposes it.

Behavior on the response:

- `enabled: true` -- nothing happens.
- `enabled: false` on an **unattended scheduled run** (no
  `-RegistrationSecret`): shows the desktop dialog telling the member to
  double-click `claude_teams_member_agent.bat`, then continues reporting
  usage as normal (that uses the separate, still-valid
  `.credentials.json` credential and is worth keeping alive).
- `enabled: false` on a **manual run of the admin-distributed `.bat`**
  (which has the Registration Secret pre-filled): re-runs
  `claude setup-token`, has the member paste the fresh token, and
  registers it as a new pool token, overwriting the local config.
- **Check failed** (network blip, 5xx): treated as unknown, not as
  disabled -- a transient outage must not nag every member hourly.

Two independent, unrelated signals are recorded — do not conflate them:

- **Usage-limit consumption** (`five_hour_utilization` /
  `seven_day_utilization`, 0-100%): a normal, temporary state that resets
  automatically at `resets_at`. Reaching 100% does not mean the token is
  invalid.
- **Token validity** (`fetch_status`): `"auth_error"` means the OAuth
  token itself has expired or been revoked (a 401 from the usage
  endpoint) — this is the "期限切れ" state shown on the admin screen.
  `"error"` covers any other failure — this does **not** mean the token
  is invalid for chat, only that its usage-limit status can't be read.

## Agent auto-update

The member agent (`scripts/claude_teams_member_agent_go`) updates itself
from a release channel, so an admin fix reaches every member without
asking anyone to re-download anything. Build and publish steps are in
that folder's `BUILD.md`; this section covers the design and its trust
model.

**Flow.** An admin builds the `.exe` with a version baked in
(`-X main.agentVersion=...`), uploads it to a private S3 bucket under a
version-scoped key, then uploads a `manifest.json` naming the `version`,
`sha256` and `key`. On every scheduled run, a member's installed copy
calls:

- `POST /claude-teams-tokens/{token_id}/agent-version` with
  `{"ingest_secret": "..."}` -- authenticated by the same per-token
  secret as the usage-snapshot push and the status check (no new
  credential), likewise un-Cognito-authenticated. It answers
  `{version, sha256, downloadUrl}`, where `downloadUrl` is a
  5-minute presigned S3 GET. A 404 means this deployment publishes no
  release, which agents treat as a silent no-op.

If the reported version differs from its own, the agent downloads it,
verifies the SHA256, and swaps it in.

**Why the bucket is private and the download is presigned.** The `.exe`
carries the org-wide Registration Secret baked in at build time (the
same exposure the old `.bat` had in plaintext). Anyone who can download
the binary can therefore add tokens to the pool via
`/claude-teams-tokens/register`. Publishing it at a guessable URL would
widen that from "members the admin handed it to" to "anyone who learns
the URL", so the download requires an `ingest_secret` -- i.e. only
machines *already in the pool* can fetch it. Note this does not make the
download defense stronger than the Registration Secret itself: a member
who already holds the binary can read the secret out of it either way.
The secret is sent in a POST body rather than a query string (unlike the
older `/status` route) so it stays out of API Gateway access logs and
corporate proxies.

**Why the SHA256 check is load-bearing.** It is what lets the presigned
URL be treated as untrusted transport. Bytes that don't match the digest
the authenticated API reported are deleted, never installed. The backend
refuses to publish a release without a digest and the agent refuses to
install one, so no path ends in unverified bytes executing on a member's
machine.

**Rollback.** Version comparison is deliberately *inequality*, not
"newer than": the manifest is the single source of truth for what
members should run, so pointing it at a previously uploaded key rolls
every member back on their next run. The bucket is versioned and
`RETAIN`ed so older builds stay available for that.

**Why no in-place restart.** The download replaces the *installed copy*
on disk and takes effect on the next scheduled run. Two renames are
used, keeping the outgoing binary as `.exe.old` (Windows permits
renaming a running image but not overwriting it), and the `.old` file is
removed on the run after that, once it is no longer the executing image.
Executing a freshly downloaded binary immediately would leave no window
in which a bad release could be noticed, for no benefit given the agent
re-runs hourly anyway. If the swap's second rename fails, the original
is restored -- a scheduled task pointing at a missing `.exe` would
silently stop reporting usage forever, which is worse than staying on an
old version.

**Failure handling.** Every step (check, download, digest, swap) warns
and gives up for that run, leaving the working version in place. Usage
reporting happens after the update attempt and is unaffected either way.
Builds with no version baked in (a bare `go build`) skip updating
entirely, since they cannot tell whether the published release differs
from themselves. `-skip-self-update` pins a machine to its current
build.

**Hand-distribution still exists** for the two cases the channel cannot
cover: bootstrap (a new member holds no `ingest_secret` yet, so cannot
call the endpoint at all) and recovery (a member whose release check is
broken).

## Behavior

- **Token selection**: round robin across enabled, non-cooling-down
  tokens (least-recently-used first).
- **Rate limit hit** (`rate_limit`/`billing_error`): the token is put in a
  5-minute cooldown and the next token is tried.
- **Invalid/revoked token** (`authentication_failed`/`oauth_org_not_allowed`):
  the token is permanently disabled (visible in the admin list) and the
  next token is tried.
- **All tokens unavailable**: the chat request fails with an HTTP 429 and a
  user-facing error message. There is no automatic fallback to a
  Bedrock-backed model.
- **Bot instructions**: passed via a `CLAUDE.md` file in a fresh temporary
  working directory per chat turn (a documented Claude Code memory
  feature), not via a forged system prompt.
- **Bot tools** (built-in tools and configured external MCP servers,
  including OAuth-authenticated ones): bridged into an in-process MCP
  server the CLI subprocess can call. Local filesystem/shell tools
  (Bash, Read, Write, Edit, Glob, Grep, WebFetch, WebSearch) are always
  disallowed for the CLI subprocess.
- **Cost display**: these chats always show `$0.00` — they don't consume
  Bedrock on-demand budget. Token usage is still recorded to the usage
  ledger (for future reporting) but never counts against the existing
  5-hour/7-day USD rate limits.

## Known limitations

- Conversation history is replayed as a flattened prompt on every turn;
  the CLI's own session (`--resume`) is not used, since Lambda's execution
  environment doesn't persist local disk across invocations reliably.
- External MCP servers using OAuth are connected through bedrock-chat's
  existing strands-based MCP client for the duration of one chat turn, not
  natively by the CLI subprocess.
- Token string rotation is delete-and-re-register only; there is no
  in-place secret update via the admin UI.
- The usage-snapshot ingest route (`POST
  /claude-teams-tokens/{token_id}/usage-snapshot`) is deliberately not
  Cognito-authenticated (see "Usage-limit tracking"); anyone holding a
  token's `ingest_secret` can write (only) usage-limit numbers for that
  token_id, and read that token's `enabled` flag via `GET
  /claude-teams-tokens/{token_id}/status`. It cannot be used to read
  chat data (including the token string itself), register/disable
  tokens, or affect chat routing. There is no in-place rotation for a
  leaked `ingest_secret` — delete and re-register the token instead.
- The self-registration route (`POST /claude-teams-tokens/register`) is
  likewise not Cognito-authenticated; anyone holding the org-wide
  Registration Secret can add a new pool token (consuming that member's
  Teams/Pro/Max quota for chat) but cannot read, list, disable, or
  delete existing tokens, and cannot affect an existing token's
  `ingest_secret`. Rotate the Registration Secret itself from the admin
  page ("Regenerate Registration Secret" /
  `POST /admin/claude-teams-tokens/regenerate-registration-secret`) if
  it leaks outside the org; already-registered tokens are unaffected.
- The self-registered Task Scheduler task (`ClaudeTeamsMemberAgent` by
  default) runs under an *Interactive* logon trigger tied to the
  member's own Windows session -- it only fires while they're logged
  in, matching how `%USERPROFILE%\.claude\.credentials.json` is
  per-user anyway. It needs no admin rights or stored password to
  register, but also means a machine that's rebooted-and-left-logged-out
  (e.g. a server) won't get scheduled usage reports until someone logs
  back in. `claude_teams_member_agent.ps1` re-registers the task
  automatically the next time it's run manually if a member deletes it.

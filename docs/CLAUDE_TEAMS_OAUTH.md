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
  the other's copy). The chat-side token (the narrow, inference-only
  one `claude setup-token` prints, pasted by the member) is sent to
  bedrock-chat exactly once, at first-run self-registration (see
  Setup) -- the same one-time transfer that pasting it into the admin
  web UI would have been.
- If the member's `.credentials.json` login doesn't carry
  `user:profile` (or the file doesn't exist), `/api/oauth/usage` calls
  from this script fail and it reports `fetch_status: "auth_error"` or
  `"error"` for the usage-tracking signal -- this does **not** mean the
  chat-side token (registered separately, from the pasted
  `setup-token` output) is invalid. On the specific `auth_error` (401)
  case, the script also shows a Windows desktop dialog (a
  `WScript.Shell` popup, auto-dismissing after 50 minutes so an
  unattended run doesn't block forever) on
  that machine prompting the member to run `claude login` (best-effort;
  a notification failure never breaks the rest of the script). Nothing
  further needs to be done beyond that -- the recurring scheduled run
  picks up the refreshed login automatically next time.

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

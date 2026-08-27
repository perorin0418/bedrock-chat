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

1. Generate one or more Claude Code OAuth tokens (Claude Pro/Max/Teams
   plan). Two options:
   - **Chat only** (no usage-limit tracking): run `claude setup-token` on
     a machine with an authenticated Claude Code CLI session. This grants
     the `user:inference` scope only.
   - **Chat + 5h/7d usage-limit tracking** (recommended): run
     `python scripts/get_full_scope_oauth_token.py` and follow the
     printed instructions. This requests the broader
     `org:create_api_key user:profile user:inference` scope, which
     additionally allows the admin screen to show live 5-hour/7-day usage
     and expiry status for the token (see "Usage-limit tracking" below).
     A token from plain `claude setup-token` still works for chat, but its
     usage-limit snapshot will always show a fetch error
     (`403 permission_error: OAuth token does not meet scope requirement
     user:profile`) since it lacks the required scope.
2. As an Admin, go to the "Claude Teams Tokens" admin page (or call
   `POST /admin/claude-teams-tokens` directly) and register each token with
   a display name. The token string is written to Secrets Manager
   (`claude-teams-token/{token_id}`) and is never shown again by any UI or
   API response.
3. Register as many tokens as you have available Teams/Pro/Max seats
   willing to share their quota. Requests round-robin across all enabled,
   not-cooling-down tokens.
4. Users select a "(Teams Plan)" model from the model picker to route their
   chat through this pool.

## Usage-limit tracking

An hourly Lambda (`claude_teams_usage_sync`) polls Anthropic's
undocumented `/api/oauth/usage` endpoint for every registered token and
records one snapshot per token in `ClaudeTeamsUsageHistoryTable`. The
admin screen shows the latest snapshot per token, and
`GET /admin/claude-teams-tokens/usage-history/csv` exports a time range
as CSV.

Two independent, unrelated signals are recorded — do not conflate them:

- **Usage-limit consumption** (`five_hour_utilization` /
  `seven_day_utilization`, 0-100%): a normal, temporary state that resets
  automatically at `resets_at`. Reaching 100% does not mean the token is
  invalid.
- **Token validity** (`fetch_status`): `"auth_error"` means the OAuth
  token itself has expired or been revoked (a 401 from the usage
  endpoint) — this is the "期限切れ" state shown on the admin screen.
  `"error"` covers any other failure, most commonly a 403
  `permission_error` because the token lacks the `user:profile` scope
  (see Setup above) — this does **not** mean the token is invalid for
  chat, only that its usage-limit status can't be read.

Because `/api/oauth/usage` requires `user:profile`, a token minted with
plain `claude setup-token` (`user:inference` only) will always show
`fetch_status: "error"` here even though chat requests work fine. Use
`scripts/get_full_scope_oauth_token.py` (see Setup) to register a token
that also has `user:profile` if you want live usage-limit tracking.

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

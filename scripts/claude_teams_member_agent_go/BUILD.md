# Building claude_teams_member_agent.exe

This folder is a self-contained Go module. An admin builds one `.exe`
with the org-wide settings baked in, then distributes just that one
file to every member -- exactly like the old `.bat` (which carried the
same two values in plaintext) except now they live inside a compiled
binary instead of an editable text file.

## Prerequisites

- Go 1.23+ (`go version` to check)
- Windows, or any OS with `GOOS=windows` cross-compilation (the build
  itself can run anywhere; the resulting `.exe` only runs on Windows --
  see "Why Windows-only" below)

## One-time setup

```powershell
cd scripts/claude_teams_member_agent_go
go mod tidy
```

## Build with the org's values baked in

Get these two values first:

- **API endpoint**: bedrock-chat's Backend API URL (the API Gateway
  one, e.g. `https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com` --
  NOT the CloudFront/frontend URL). Find it in the CDK deploy output
  (`BackendApiBackendApiUrl...`) or CloudFormation stack outputs.
- **Registration secret**: from the bedrock-chat admin page ->
  "Claude Teams Tokens" -> "Registration Secret".

Then build:

```powershell
$ApiEndpoint = "https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com"
$RegSecret   = "the-actual-registration-secret"

$env:GOOS = "windows"
$env:GOARCH = "amd64"
go build -ldflags "-X main.defaultAPIEndpoint=$ApiEndpoint -X main.defaultRegistrationSecret=$RegSecret" `
    -o claude_teams_member_agent.exe .
```

That's it -- hand `claude_teams_member_agent.exe` to every member.
They double-click it once (same as the old `.bat`); everything else
(browser approval for `claude setup-token`, self-registration, the
recurring Task Scheduler entry) happens automatically.

## Verifying the build

```powershell
.\claude_teams_member_agent.exe -help
```

Confirms the flags exist and the exe runs. It won't reveal the baked-in
secret (`-help` only prints flag descriptions, not resolved values).

To confirm the values actually got baked in without running the full
first-run flow (which requires an interactive `claude setup-token`
browser approval), pass an *empty* override and confirm the program
still resolves a non-placeholder endpoint from its own default: run
with `-registration-secret ""` and check the very first error message
mentions your actual API host, not "is empty or still a placeholder".

## Why Windows-only

This tool only ever targets Windows: `notify_windows.go` calls
`user32.dll` directly via `syscall` (there is no cross-platform
equivalent that doesn't add a dependency), and the whole point of this
program is registering a Windows Task Scheduler entry via `schtasks.exe`.
`GOOS=windows` is required at build time regardless of what platform
you build it *from*.

## Rebuilding after a Registration Secret rotation

If an admin regenerates the Registration Secret (bedrock-chat admin
page -> "Regenerate"), existing members' already-registered tokens are
unaffected (see docs/CLAUDE_TEAMS_OAUTH.md) -- only *new* member
registrations need the new secret. Rebuild the `.exe` with the new
value and distribute it to any new members only; existing members do
not need to update anything.

## Migrating from the old .ps1/.bat pair

A member's existing `%USERPROFILE%\.claude\claude_teams_member_agent.config.json`
(written by the old `claude_teams_member_agent.ps1`) is read as-is by
this `.exe` -- same file path, same JSON shape (snake_case
`token_id`/`ingest_secret`). Just distribute the new `.exe`; no
re-registration needed. See the top-of-file comment in `main.go` for
the full rationale for this rewrite.

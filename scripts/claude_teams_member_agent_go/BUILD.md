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

## Why the scheduled run goes through a generated `.vbs`, not the `.exe` directly

The Task Scheduler entry this program registers for itself (see
`registerSelfAsScheduledTask` in `scheduler.go`) points at
`wscript.exe` running a small `.vbs` this program writes next to its
own installed copy, rather than at the `.exe` directly. That `.vbs`
launches the `.exe` via `WScript.Shell.Run` with `WindowStyle=0`
(hidden), which is the only way found that avoids a console window
ever being created for that scheduled run:

- Plain `schtasks /Create` has no window-visibility option of its own.
- Building the whole program with `-H=windowsgui` was considered and
  rejected: that removes the console for *every* run mode, not just
  the scheduled one, silently swallowing the interactive first-run
  flow's `claude setup-token` browser-approval prompt and any error
  output too.
- A runtime `GetConsoleWindow` + `ShowWindow(SW_HIDE)` call (hiding the
  console from inside the program itself, gated on `-unattended`) was
  also tried and rejected: Windows still creates and briefly shows the
  console before the program's own code gets a chance to run and hide
  it, so a visible flash remained on every scheduled run -- exactly
  what this feature exists to prevent.

The `.vbs` is deliberately kept to one `WScript.Shell.Run` line (see
`writeLauncherVBScript`) and is regenerated on every run right
alongside the installed-copy self-update, so a rebuilt/redistributed
`.exe` (or a changed `-task-interval-minutes`/`-task-name`) always gets
a matching, up-to-date launcher automatically.

Members already registered before this launcher indirection existed
have an existing Task Scheduler entry whose `/TR` points at the `.exe`
directly. `registerSelfAsScheduledTask` detects this on their very next
run (manual or scheduled) by inspecting the existing entry's actual
`schtasks /Query /XML` action (`schtasksActionNeedsUpdate`) and
recreates it to point at the launcher instead -- no separate migration
step, uninstall, or admin action needed; it self-upgrades.

## Rebuilding after a Registration Secret rotation

If an admin regenerates the Registration Secret (bedrock-chat admin
page -> "Regenerate"), existing members' already-registered tokens are
unaffected (see docs/CLAUDE_TEAMS_OAUTH.md) -- only *new* member
registrations need the new secret. Rebuild the `.exe` with the new
value and distribute it to any new members only; existing members do
not need to update anything.

## Publishing an update to already-registered members

Members' installed copies auto-update. On every scheduled run, the
installed `.exe` asks bedrock-chat which version it should be on and
replaces itself if the answer differs (see `updater.go`). Rolling out a
fix therefore means publishing a build, not chasing members to
double-click anything.

```bash
# 1. Build (build.bat bakes in a version and prints it plus the SHA256).
scripts/claude_teams_member_agent_go/build.bat

# 2. Publish. The bucket name comes from the CDK output
#    `ClaudeTeamsAgentReleaseBucketName`.
scripts/publish_claude_teams_agent_release.sh \
    --bucket  bedrock-chat-claudeteamsagentreleasebucket-xxxxxxxx \
    --exe     scripts/claude_teams_member_agent_go/claude_teams_member_agent.exe \
    --version 2026.09.10-1430-abc1234
```

The `--version` must match what the binary reports
(`claude_teams_member_agent.exe -version`); the publish script checks
this where it can, because a mismatch would make every member
re-download the same binary on every run forever.

How it works, and why it is built this way:

- **The manifest is the source of truth.** `manifest.json` at the
  bucket root names the `version`, `sha256` and object `key` members
  should be on. Agents compare it to their own baked-in version for
  *inequality*, not "newer than", so **rolling back is just pointing the
  manifest at a previously uploaded key** and every member follows on
  their next run. Releases are uploaded under version-scoped keys and
  the bucket is versioned + `RETAIN`ed so older builds stay available
  for exactly that.
- **The bucket is private and the download is a short-lived presigned
  URL**, minted only by
  `POST /claude-teams-tokens/{token_id}/agent-version` after the caller
  presents a valid per-token `ingest_secret`. This matters because the
  `.exe` carries the org-wide Registration Secret baked in: a publicly
  readable object would let anyone who learns the URL obtain that secret
  and add tokens to the pool. Only machines already registered in the
  pool can download the binary. The secret travels in the POST body,
  not a query string, to keep it out of access logs and proxies.
- **The agent verifies the SHA256 before installing.** That check is
  what makes the download URL safe to treat as untrusted transport: bytes
  that don't match the digest the authenticated API reported are deleted,
  never installed. The backend refuses to publish a release with no
  digest, and the agent refuses to install one, so there is no path that
  ends in unverified bytes executing on a member's machine.
- **Nothing restarts mid-run.** The new binary replaces the installed
  copy on disk (two renames, keeping the outgoing one as `.exe.old`, since
  Windows permits renaming but not overwriting a running image) and takes
  effect on the *next* scheduled run. The `.old` file is removed the run
  after that, when it is no longer the executing image.
- **Every failure is non-fatal.** A failed check, download, digest
  mismatch, or swap logs a warning, leaves the working version in place,
  and retries next run. Usage reporting is unaffected either way.

If a deployment publishes no manifest, the endpoint answers 404 and
agents silently do nothing -- hand-distribution (below) keeps working
unchanged.

To pin one machine to its current build (e.g. to reproduce a bug), run
it with `-skip-self-update`.

## Hand-distributing an updated `.exe` (bootstrap and recovery)

Every run started from a location other than the installed copy
itself (i.e. any manual double-click of the admin-distributed `.exe`)
re-checks the installed copy at
`%USERPROFILE%\.claude\claude_teams_member_agent\claude_teams_member_agent.exe`
and overwrites it whenever its file size differs from the `.exe` being
run (see `copySelfToFixedLocation` / `installedCopyNeedsUpdate` in
`scheduler.go`). So distributing a rebuilt `.exe` to an
already-registered member (e.g. after a bug fix) needs no
uninstall/manual-copy step on their end: hand them the new `.exe` and
have them double-click it once; it updates the installed copy and the
existing scheduled task then keeps running the new version, with no
re-registration needed. (The scheduled re-run itself runs the
installed copy directly, so it has no separate newer source to update
itself from -- a member always needs to double-click the
newly-distributed `.exe` at least once for this particular path to take
effect -- which is why the release channel above exists.)

This path is still needed, and still supported, for two things the
release channel structurally cannot cover:

- **Bootstrap.** A brand-new member has no local config yet, so they
  hold no `ingest_secret` and cannot call the authenticated version
  endpoint at all. The first `.exe` always arrives by hand.
- **Recovery.** If a member's release check is failing (network policy,
  a bad manifest, a bug in the updater itself), handing them a fresh
  `.exe` to double-click still fixes them.

One consequence worth knowing: because this path compares file *size*
rather than version, double-clicking an *older* distributed `.exe`
downgrades the installed copy. That is self-correcting -- the same run
then checks the release channel and pulls the published version back
down.

## Migrating from the old .ps1/.bat pair

A member's existing `%USERPROFILE%\.claude\claude_teams_member_agent.config.json`
(written by the old `claude_teams_member_agent.ps1`) is read as-is by
this `.exe` -- same file path, same JSON shape (snake_case
`token_id`/`ingest_secret`). Just distribute the new `.exe`; no
re-registration needed. See the top-of-file comment in `main.go` for
the full rationale for this rewrite.

## If a member sees "could not find the Claude Code CLI"

The first-run flow shells out to `claude setup-token`. Earlier versions
invoked it by bare name only, so it failed with
`exec: "claude": executable file not found in %PATH%` whenever the CLI
was installed but not visible on `%PATH%` -- most commonly because the
native installer's `setx` PATH change only reaches *newly created*
processes, so any already-open console (or a Task Scheduler run) never
sees it, or because an `npm -g` install lives in `%APPDATA%\npm` and
that directory isn't on the user's PATH.

Resolution now falls back automatically (see `claudecli.go`):

1. `-claude-path` flag, else the `CLAUDE_TEAMS_AGENT_CLAUDE_PATH`
   environment variable, if either is set. An explicit value that
   doesn't exist is a hard error -- it is never silently ignored.
2. `%PATH%` (unchanged previous behavior; a healthy install still
   takes this route).
3. Well-known install directories: `%USERPROFILE%\.local\bin`,
   `%USERPROFILE%\.claude\local`, `%USERPROFILE%\.claude\bin`,
   `%APPDATA%\npm`, `%LOCALAPPDATA%\npm`,
   `%LOCALAPPDATA%\Programs\claude`, `%ProgramFiles%\nodejs`.

Only `claude.exe`, `claude.cmd` and `claude.bat` are considered; the
extensionless `claude` next to a Windows native install is a POSIX
shell script that `CreateProcess` cannot launch. A `.cmd`/`.bat` entry
point is run through `cmd.exe /c` automatically.

If all of that still fails, the member gets an error listing every
directory searched plus a `where claude` hint, and can pin the path:

```powershell
.\claude_teams_member_agent.exe -claude-path "C:\Users\you\.local\bin\claude.exe"
```

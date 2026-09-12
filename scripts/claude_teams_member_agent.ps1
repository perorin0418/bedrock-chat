#requires -Version 5.1
<#
.SYNOPSIS
    One-shot setup + recurring reporting agent for a Claude Teams pool
    member: on first run, obtains a Claude Code OAuth token, self-registers
    it with bedrock-chat, AND registers itself as a recurring Windows Task
    Scheduler entry; every run (including that first one) also reports
    this machine's current 5-hour/7-day usage-limit status. A single
    script, zero manual Task Scheduler setup.

.DESCRIPTION
    Combines what used to be several separate steps into one:

      1. **First run only** (no local config file next to this script
         yet): runs `claude setup-token` interactively (opens a browser
         for the member to approve, same as any other Claude Code
         login), then reads the resulting accessToken from
         `%USERPROFILE%\.claude\.credentials.json` and self-registers
         it as a new Claude Teams pool token via
         `POST /claude-teams-tokens/register`, authenticated by the
         org-wide `-RegistrationSecret` (ask your admin for this
         value). The returned `token_id`/`ingest_secret` are saved to
         a local JSON config file next to this script so this step is
         never repeated.
      2. **Whenever no matching Task Scheduler entry exists yet**
         (checked on every run, so it's re-created if a member deletes
         it): registers a recurring Task Scheduler task that re-runs
         this exact script (without `-RegistrationSecret`, which is no
         longer needed once step 1 has succeeded) on `-TaskIntervalMinutes`
         (default 60). Runs in the current user's own logon session --
         no admin rights or stored password needed, and it naturally
         only fires while the member is logged in (matching how
         `%USERPROFILE%\.claude\.credentials.json` is per-user anyway).
      3. **Every run**: reads the saved config, queries Anthropic's
         undocumented `/api/oauth/usage` endpoint using this machine's
         current Claude Code accessToken, and pushes the resulting
         5-hour/7-day utilization to
         `POST /claude-teams-tokens/{token_id}/usage-snapshot`,
         authenticated by that token's own `ingest_secret`.

    bedrock-chat never receives or stores this member's refreshToken --
    only the chat-side accessToken once (step 1, same as pasting it into
    the admin web UI would have done) and the resulting usage
    percentages on every run (step 3). It never re-reads or refreshes
    this member's local credentials file, so it can't race the member's
    own Claude Code CLI over which side holds the "current" refresh
    token (see docs/CLAUDE_TEAMS_OAUTH.md).

    A member only ever has to run this script manually once (the
    interactive `claude setup-token` browser step); everything after
    that, including the recurring schedule itself, is set up
    automatically.

.PARAMETER RegistrationSecret
    The org-wide self-registration secret an admin gets from
    "Claude Teams Tokens" > "Registration Secret" and gives to every
    member running this script. Only needed/used on the first run
    (ignored, if provided, on later runs -- the config file already has
    everything needed by then).

.PARAMETER ApiEndpoint
    bedrock-chat's backend API base URL, e.g.
    https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com

.PARAMETER DisplayName
    Name shown for this token on the admin page. Only used/resolved on
    the first run (ignored, if provided, on later runs). Defaults to
    the Claude account's email address (read from
    %USERPROFILE%\.claude.json's oauthAccount.emailAddress, the same
    file the Claude Code CLI itself writes on login) so admins can tell
    at a glance whose Claude account each pooled token belongs to. If
    that file/field isn't available, the script interactively asks the
    member to type a display name instead of silently guessing one; an
    empty answer aborts the script rather than falling back to an
    unrecognizable hostname/username string.

.PARAMETER TaskIntervalMinutes
    How often the self-registered Task Scheduler entry re-runs this
    script. Defaults to 60 (hourly). Only read when (re-)creating the
    task; changing it after the task already exists has no effect
    unless the task is deleted first (it'll be recreated with the new
    interval on the next manual run).

.PARAMETER TaskName
    Name of the Task Scheduler entry this script registers/looks for.
    Defaults to a fixed name so re-running the script (or the task
    itself running) never creates duplicates.

.PARAMETER SkipTaskRegistration
    Skip the Task Scheduler self-registration step entirely (e.g. if an
    admin prefers to manage the recurring schedule some other way, or
    you're just testing usage reporting manually).

.PARAMETER ConfigPath
    Where to save/read this script's own state (token_id, ingest_secret)
    after first-run registration. Defaults to a fixed per-user location,
    `%USERPROFILE%\.claude\claude_teams_member_agent.config.json` --
    deliberately NOT next to the script/.bat, so re-distributing the
    script to a different folder (or a member moving it) doesn't lose
    track of an already-registered token and mint a duplicate one.

.PARAMETER CredentialsPath
    Override the local Claude Code CLI credentials file path used ONLY
    for the usage-limit tracking lookup (/api/oauth/usage on every
    scheduled run) -- unrelated to the chat-side token, which comes
    from pasting 'claude setup-token' output at first run instead.
    Defaults to `%USERPROFILE%\.claude\.credentials.json`.

.EXAMPLE
    The only manual step, ever (interactive, one time):
        .\claude_teams_member_agent.ps1 `
            -RegistrationSecret "the-secret-from-the-admin-page" `
            -ApiEndpoint "https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com"

    This single run obtains the chat token, registers it, AND sets up
    its own hourly Task Scheduler entry. Nothing further needs to be
    configured -- Task Scheduler takes it from here.
#>

param(
    [string]$RegistrationSecret,
    [Parameter(Mandatory)][string]$ApiEndpoint,
    [string]$DisplayName,
    [int]$TaskIntervalMinutes = 60,
    [string]$TaskName = 'ClaudeTeamsMemberAgent',
    [switch]$SkipTaskRegistration,
    [string]$ConfigPath,
    [string]$CredentialsPath = "$env:USERPROFILE\.claude\.credentials.json"
)

$ErrorActionPreference = 'Stop'

# Fixed per-user location, NOT next to the script (%PSScriptRoot%
# / .bat's folder). If the admin ever re-distributes an updated copy of
# the .bat/.ps1 pair to a different folder, or a member moves/renames
# the folder they were given, a script-relative path would silently
# "lose" the previous registration and mint a duplicate pool token
# instead of resuming the existing one. Anchoring to
# %USERPROFILE%\.claude (the same directory Claude Code CLI itself
# uses for .credentials.json) keeps this file discoverable regardless
# of where the script currently lives.
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path "$env:USERPROFILE\.claude" 'claude_teams_member_agent.config.json'
}

# Same reasoning as $ConfigPath above for the .bat -> powershell.exe -File
# invocation quirks. Actually resolving $DisplayName (email lookup /
# interactive prompt) happens later, inside Register-ThisMachine -- it's
# only needed on a genuine first run. Doing it here unconditionally would
# also run on every unattended Task Scheduler re-run of an already
# registered machine, where a Read-Host prompt would hang forever with
# no one there to answer it.

# Catches the most common first-run mistake: running the .bat wrapper
# without editing its placeholder values first. Without this check, a
# stray "PASTE_API_ENDPOINT_HERE" produces a confusing low-level DNS
# error ("リモート名を解決できませんでした") deep inside Invoke-RestMethod
# instead of telling the member what to actually fix.
if ($ApiEndpoint -match '(?i)PASTE_API_ENDPOINT_HERE|^$') {
    Write-Host "ERROR: -ApiEndpoint is still the placeholder value ('$ApiEndpoint')." -ForegroundColor Red
    Write-Host "Edit claude_teams_member_agent.bat (or pass -ApiEndpoint directly) with your admin's actual bedrock-chat API URL, then run this again." -ForegroundColor Red
    exit 1
}
if (-not [string]::IsNullOrWhiteSpace($RegistrationSecret) -and $RegistrationSecret -match '(?i)PASTE_REGISTRATION_SECRET_HERE') {
    Write-Host "ERROR: -RegistrationSecret is still the placeholder value." -ForegroundColor Red
    Write-Host "Edit claude_teams_member_agent.bat (or pass -RegistrationSecret directly) with your admin's actual Registration Secret, then run this again." -ForegroundColor Red
    exit 1
}

# Common first-run mistake: pasting the API Gateway host without its
# "https://" scheme (e.g. copied from a URL bar's address-only view, or
# CloudFormation output text). Without a scheme, Invoke-RestMethod fails
# with a generic "リモート サーバーに接続できません" / "Unable to connect
# to the remote server" that gives no hint about what's actually wrong,
# so fail fast here with an actionable message instead of silently
# guessing what scheme was meant.
if ($ApiEndpoint -notmatch '^https?://') {
    Write-Host "ERROR: -ApiEndpoint ('$ApiEndpoint') must start with http:// or https://." -ForegroundColor Red
    Write-Host "Ask your admin for the bedrock-chat Backend API URL (the API Gateway one, e.g. https://xxxxxxxxxx.execute-api.<region>.amazonaws.com -- NOT the CloudFront/frontend URL) and fix claude_teams_member_agent.bat." -ForegroundColor Red
    exit 1
}

$UsageApiUrl = 'https://api.anthropic.com/api/oauth/usage'
$AnthropicVersion = '2023-06-01'
$OauthBetaHeader = 'oauth-2025-04-20'
$RequestTimeoutSeconds = 15

function Get-ApiBase {
    return $ApiEndpoint.TrimEnd('/')
}

function Get-LocalAccessToken {
    if (-not (Test-Path -LiteralPath $CredentialsPath)) {
        throw "Claude Code credentials file not found at: $CredentialsPath"
    }
    $raw = Get-Content -LiteralPath $CredentialsPath -Raw | ConvertFrom-Json
    $oauth = $raw.claudeAiOauth
    if (-not $oauth -or -not $oauth.accessToken) {
        throw "No claudeAiOauth.accessToken found in $CredentialsPath"
    }
    return $oauth.accessToken
}

function Read-LocalConfig {
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        return $null
    }
    return Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
}

function Write-LocalConfig {
    param([string]$TokenId, [string]$IngestSecret)
    $configDir = Split-Path -Parent $ConfigPath
    if (-not (Test-Path -LiteralPath $configDir)) {
        # Normally already created by the Claude Code CLI itself (this
        # defaults under %USERPROFILE%\.claude), but don't assume it --
        # a custom -ConfigPath could point anywhere.
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }
    $config = @{
        token_id      = $TokenId
        ingest_secret = $IngestSecret
    }
    ($config | ConvertTo-Json) | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
}

function Resolve-DisplayName {
    # Tries to pick a display name that lets an admin recognize whose
    # seat a pooled token belongs to. Only ever called on a genuine first
    # run (see Register-ThisMachine), so an interactive Read-Host prompt
    # here is safe -- the member is already sitting at the console
    # approving 'claude setup-token' in a browser moments earlier.
    $claudeJsonPath = "$env:USERPROFILE\.claude.json"
    if (Test-Path -LiteralPath $claudeJsonPath) {
        try {
            # NOTE: deliberately NOT using ConvertFrom-Json here. This file
            # accumulates large nested config/prompt blobs over time and,
            # observed in practice, exceeds what PowerShell 5.1's JSON
            # parser accepts ("Invalid object passed in, ':' or '}'
            # expected") on accounts with enough history -- ConvertFrom-Json
            # would silently fail via the catch below and this feature
            # would never actually fire. A plain regex pull of one known
            # field is robust to that and to unrelated schema changes
            # elsewhere in the file.
            $raw = Get-Content -LiteralPath $claudeJsonPath -Raw
            if ($raw -match '"emailAddress"\s*:\s*"([^"]*)"') {
                $email = $Matches[1]
                if (-not [string]::IsNullOrWhiteSpace($email)) {
                    return $email
                }
            }
        }
        catch {
            Write-Host "WARNING: could not read emailAddress from $claudeJsonPath ($($_.Exception.Message))." -ForegroundColor Yellow
        }
    }

    # No emailAddress found (missing file, unreadable, or field absent) --
    # ask the member directly rather than silently falling back to a
    # hostname/username string an admin may not recognize. An empty
    # answer is treated as a hard failure, not a silent fallback: an
    # unrecognizable "hostname-username" entry on the admin page is
    # exactly the confusing state this prompt exists to avoid.
    Write-Host "Could not automatically determine your Claude account email from $claudeJsonPath." -ForegroundColor Yellow
    $entered = Read-Host "Enter a display name for this token (e.g. your Claude account email)"
    if ([string]::IsNullOrWhiteSpace($entered)) {
        throw "No display name entered. Re-run this script and enter a display name (e.g. your Claude account email) when prompted."
    }
    return $entered
}

function Register-ThisMachine {
    if ([string]::IsNullOrWhiteSpace($RegistrationSecret)) {
        throw "No local config found at $ConfigPath and -RegistrationSecret was not provided. Ask your admin for the registration secret (Claude Teams Tokens > Registration Secret) and pass it as -RegistrationSecret on this first run."
    }

    Write-Host "First run: no local config found. Setting up this machine as a new Claude Teams pool token..." -ForegroundColor Cyan
    Write-Host ""

    # Deliberately does NOT read %USERPROFILE%\.claude\.credentials.json
    # here, even if it already exists. 'claude setup-token' mints a
    # long-lived, inference-only-scoped token and PRINTS it to this
    # terminal -- per Anthropic's own docs, it never saves it anywhere
    # (not to .credentials.json, not to any file). A pre-existing
    # .credentials.json is written by the normal interactive
    # `claude login`/`/login` flow instead, and typically carries a much
    # broader scope (user:profile, org:create_api_key, etc. depending on
    # the account) -- silently reusing that file here, as an earlier
    # version of this script did, would send a broader-scoped credential
    # to bedrock-chat than intended without the member realizing it.
    # Always running setup-token and having the member paste its output
    # guarantees the token actually sent is the narrow-scope one.
    Write-Host "Running 'claude setup-token' -- a browser window will open for you to approve." -ForegroundColor Yellow
    Write-Host "It will print a long-lived OAuth token to this terminal when done. It does NOT save that token anywhere -- copy it, you'll be asked to paste it below." -ForegroundColor Yellow
    & claude setup-token
    if ($LASTEXITCODE -ne 0) {
        throw "'claude setup-token' exited with code $LASTEXITCODE. Fix the login and re-run this script."
    }
    Write-Host ""

    # -AsSecureString so the token itself isn't echoed back to the
    # console as the member types/pastes it; converted to plain text
    # only for the instant it's needed to build the request body below.
    # Asked immediately after 'claude setup-token' prints its token
    # (before Resolve-DisplayName's own possible prompt below) so the
    # member pastes it while it's still fresh on screen, instead of a
    # second prompt intervening in between.
    $chatTokenSecure = Read-Host -Prompt "Paste the OAuth token 'claude setup-token' printed above" -AsSecureString
    $chatToken = [System.Net.NetworkCredential]::new('', $chatTokenSecure).Password
    if ([string]::IsNullOrWhiteSpace($chatToken)) {
        throw "No token entered. Re-run this script (re-running 'claude setup-token' if needed) and paste the printed token when prompted."
    }

    if ([string]::IsNullOrWhiteSpace($DisplayName)) {
        # Assign via $script: scope, not a plain local $DisplayName = ...
        # -- this function is called from top-level script code, but
        # PowerShell functions still get their own local scope, so a bare
        # assignment here would only shadow the outer $DisplayName for the
        # rest of this function and leave the top-level (and
        # Register-SelfAsScheduledTask, called after this returns) still
        # seeing it as blank.
        $script:DisplayName = Resolve-DisplayName
    }

    $body = @{
        registration_secret = $RegistrationSecret
        display_name         = $DisplayName
        token_value           = $chatToken
    } | ConvertTo-Json -Compress

    $response = Invoke-RestMethod -Uri "$(Get-ApiBase)/claude-teams-tokens/register" -Method Post `
        -ContentType 'application/json' `
        -Body $body `
        -TimeoutSec $RequestTimeoutSeconds

    # Note: the API returns camelCase field names (tokenId/ingestSecret),
    # not the snake_case used in the request body -- bedrock-chat's
    # schemas serialize responses via humps.camelize (see
    # backend/app/routes/schemas/base.py). Referencing token_id here
    # instead of tokenId silently reads $null and produces a garbled
    # "Registered as token_id=" message plus a broken saved config.
    Write-LocalConfig -TokenId $response.tokenId -IngestSecret $response.ingestSecret
    Write-Host "Registered as token_id=$($response.tokenId). Saved local config to $ConfigPath." -ForegroundColor Green
    Write-Host ""

    return $response
}

function Invoke-UsageIngest {
    param([string]$TokenId, [string]$IngestSecret, [hashtable]$Body)
    $Body['ingest_secret'] = $IngestSecret
    $bodyJson = $Body | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri "$(Get-ApiBase)/claude-teams-tokens/$TokenId/usage-snapshot" -Method Post `
        -ContentType 'application/json' `
        -Body $bodyJson `
        -TimeoutSec $RequestTimeoutSeconds | Out-Null
}

function Get-ChatTokenStatus {
    # Asks bedrock-chat whether the chat-side token this machine
    # registered (the one pasted from 'claude setup-token' output, held
    # server-side only) is still usable. This is deliberately a
    # server-side question: the token string itself is NOT stored on this
    # machine -- 'claude setup-token' only printed it once -- so this
    # script cannot check its expiry locally, and the endpoint never
    # returns the token string either (only {enabled: bool}).
    #
    # Returns $true/$false, or $null when the check itself couldn't be
    # completed (network blip, 5xx, ...). $null must NOT be treated as
    # "disabled": a transient outage would otherwise nag every member
    # with a dialog every hour.
    param([string]$TokenId, [string]$IngestSecret)
    try {
        $escapedSecret = [System.Uri]::EscapeDataString($IngestSecret)
        $response = Invoke-RestMethod -Method Get -TimeoutSec $RequestTimeoutSeconds `
            -Uri "$(Get-ApiBase)/claude-teams-tokens/$TokenId/status?ingest_secret=$escapedSecret"
        return [bool]$response.enabled
    }
    catch {
        Write-Host "WARNING: could not check chat-token status with bedrock-chat: $($_.Exception.Message)" -ForegroundColor Yellow
        return $null
    }
}

function Show-UserNotification {
    param([string]$Title, [string]$Message)
    # Best-effort only: this runs unattended from Task Scheduler most of
    # the time, so there's no guarantee anyone is at the keyboard to see
    # a console Write-Host. A balloon/toast notification is easy to miss
    # or dismiss without reading, so this uses an actual modal dialog
    # (WScript.Shell's Popup, a built-in Windows COM object -- no extra
    # module needed) that stays on top until acknowledged. It has a
    # timeout so an unattended run that nobody is at the keyboard for
    # doesn't leave a dialog open forever and block/pile up against the
    # next scheduled run. Never lets a notification failure (e.g. no
    # desktop session, some locked-down environment) break the rest of
    # the script -- this is purely informational.
    try {
        $shell = New-Object -ComObject WScript.Shell
        # Popup(Text, SecondsToWait, Title, Type) -- 0x30 = vbExclamation
        # icon + OK button; auto-dismisses after SecondsToWait if nobody
        # answers, returning -1 (timeout), which this doesn't need to
        # distinguish from a real OK click. 3000s (50min) gives it a real
        # chance to be noticed within the default hourly scheduled-run
        # interval without still being open when the next run starts.
        $shell.Popup($Message, 3000, $Title, 0x30) | Out-Null
    }
    catch {
        Write-Host "(Could not show a desktop notification: $($_.Exception.Message))" -ForegroundColor DarkYellow
    }
}

function Copy-SelfToFixedLocation {
    # Copies this .ps1 (and its .bat wrapper, if found alongside it) to
    # a fixed per-user location so the Task Scheduler entry keeps
    # working even if the admin-distributed folder this script was
    # originally run from gets deleted, moved, or was on removable/network
    # media. Mirrors the reasoning already applied to $ConfigPath above.
    # Returns the path the Task Scheduler action should point at
    # (the copy on success, or the original $PSCommandPath as a
    # best-effort fallback if copying fails for any reason).
    $installDir = "$env:USERPROFILE\.claude\claude_teams_member_agent"
    $installedScriptPath = Join-Path $installDir 'claude_teams_member_agent.ps1'

    if ($PSCommandPath -eq $installedScriptPath) {
        # Already running from the fixed location (e.g. a Task Scheduler
        # re-run after a previous first-run copy) -- nothing to do.
        return $PSCommandPath
    }

    try {
        if (-not (Test-Path -LiteralPath $installDir)) {
            New-Item -ItemType Directory -Path $installDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $PSCommandPath -Destination $installedScriptPath -Force

        $sourceBat = Join-Path (Split-Path -Parent $PSCommandPath) 'claude_teams_member_agent.bat'
        if (Test-Path -LiteralPath $sourceBat) {
            Copy-Item -LiteralPath $sourceBat -Destination (Join-Path $installDir 'claude_teams_member_agent.bat') -Force
        }

        Write-Host "Copied this script (and its .bat, if present) to $installDir so the scheduled task keeps working even if this original folder is later moved or deleted." -ForegroundColor Cyan
        return $installedScriptPath
    }
    catch {
        Write-Host "WARNING: could not copy this script to a fixed location ($($_.Exception.Message)). The scheduled task will point at this script's current path instead; if this folder is later moved or deleted, re-run the script from wherever it ends up." -ForegroundColor Yellow
        return $PSCommandPath
    }
}

function Register-SelfAsScheduledTask {
    # Re-runs this exact script on a recurring interval via Task
    # Scheduler, without -RegistrationSecret (already consumed) so the
    # secret doesn't sit indefinitely in a Task Scheduler action's saved
    # arguments. Idempotent: does nothing if $TaskName already exists,
    # so calling this on every run is safe and self-healing if a member
    # accidentally deletes the task.
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        return
    }

    Write-Host "Registering Task Scheduler entry '$TaskName' (every $TaskIntervalMinutes minute(s))..." -ForegroundColor Cyan

    $scriptPath = Copy-SelfToFixedLocation
    $argumentList = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$scriptPath`""
        '-ApiEndpoint', "`"$ApiEndpoint`""
        '-DisplayName', "`"$DisplayName`""
        '-TaskIntervalMinutes', "$TaskIntervalMinutes"
        '-TaskName', "`"$TaskName`""
        '-ConfigPath', "`"$ConfigPath`""
        '-CredentialsPath', "`"$CredentialsPath`""
    ) -join ' '

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argumentList
    # No -RepetitionDuration: omitting it means "repeat indefinitely".
    # Passing [TimeSpan]::MaxValue explicitly fails registration with
    # "task XML contains a value which is incorrectly formatted or out
    # of range" (its ISO-8601 duration serialization overflows the
    # format Task Scheduler's XML schema accepts).
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $TaskIntervalMinutes)
    # Interactive logon type (not S4U/password-based): runs as the
    # current Windows user whenever they're logged on, needs no stored
    # credential, and naturally has access to their own
    # %USERPROFILE%\.claude\.credentials.json.
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Force | Out-Null
        Write-Host "Task Scheduler entry '$TaskName' registered." -ForegroundColor Green
    }
    catch {
        Write-Host "WARNING: could not auto-register Task Scheduler entry ($($_.Exception.Message)). Usage was still reported for this run; re-run this script later, or set up the recurring task manually, to keep reporting going." -ForegroundColor Yellow
    }
    Write-Host ""
}

# --- Main ---

$config = Read-LocalConfig
$justRegistered = $false
if ($null -eq $config) {
    Register-ThisMachine | Out-Null
    $justRegistered = $true
    # Re-read rather than use Register-ThisMachine's return value
    # directly: that return value is the raw API response (camelCase
    # tokenId/ingestSecret), while everywhere else in this script reads
    # the locally-saved config shape (snake_case token_id/ingest_secret,
    # matching what Write-LocalConfig just wrote). Mixing the two naming
    # conventions here previously left $tokenId/$ingestSecret silently
    # $null on a fresh first run.
    $config = Read-LocalConfig
}
$tokenId = $config.token_id
$ingestSecret = $config.ingest_secret

# Is the chat-side token this machine registered still usable server-side?
# Skipped when we *just* registered above (it's trivially fresh, and the
# member is still at the console -- no reason to nag them).
if (-not $justRegistered) {
    $chatTokenEnabled = Get-ChatTokenStatus -TokenId $tokenId -IngestSecret $ingestSecret
    # Only $false acts; $null means the check itself failed and must not
    # be mistaken for "expired" (see Get-ChatTokenStatus).
    if ($chatTokenEnabled -eq $false) {
        if (-not [string]::IsNullOrWhiteSpace($RegistrationSecret)) {
            # A manual run via the admin-distributed .bat (which has the
            # registration secret pre-filled) can fix this on the spot:
            # mint a fresh token and re-register it against a new
            # token_id, overwriting the local config.
            Write-Host "This machine's chat-side token is no longer usable by bedrock-chat. Re-registering a fresh one..." -ForegroundColor Yellow
            Write-Host ""
            Register-ThisMachine | Out-Null
            $config = Read-LocalConfig
            $tokenId = $config.token_id
            $ingestSecret = $config.ingest_secret
        }
        else {
            # Unattended scheduled run: nobody passed the registration
            # secret, so this can't self-heal. Tell the member what to
            # do, then carry on reporting usage (which uses a different,
            # still-valid local credential and is worth keeping alive).
            Write-Host "WARNING: bedrock-chat reports this machine's chat-side token as no longer usable. Re-run claude_teams_member_agent.bat to register a fresh one." -ForegroundColor Yellow
            Show-UserNotification -Title 'Claude Teams: chat token needs renewing' `
                -Message "The Claude token you shared with bedrock-chat for chat has stopped working (expired or revoked), so your seat is no longer serving chat requests. To fix it, double-click claude_teams_member_agent.bat in $env:USERPROFILE\.claude\claude_teams_member_agent -- it will run 'claude setup-token' and register the new token for you. Usage reporting keeps working in the meantime."
        }
    }
}

if (-not $SkipTaskRegistration) {
    Register-SelfAsScheduledTask
}

$sampledAtMs = [long]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())

try {
    $accessToken = Get-LocalAccessToken
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

try {
    $response = Invoke-RestMethod -Uri $UsageApiUrl -Method Get `
        -Headers @{
            'Authorization'    = "Bearer $accessToken"
            'anthropic-version' = $AnthropicVersion
            'anthropic-beta'    = $OauthBetaHeader
        } `
        -TimeoutSec $RequestTimeoutSeconds

    $fiveHour = $response.five_hour
    $sevenDay = $response.seven_day

    Invoke-UsageIngest -TokenId $tokenId -IngestSecret $ingestSecret -Body @{
        fetch_status           = 'ok'
        five_hour_utilization  = $fiveHour.utilization
        five_hour_resets_at    = $fiveHour.resets_at
        seven_day_utilization  = $sevenDay.utilization
        seven_day_resets_at    = $sevenDay.resets_at
        sampled_at_ms          = $sampledAtMs
    }
    Write-Host "OK: reported 5h=$($fiveHour.utilization)% 7d=$($sevenDay.utilization)% for token_id=$tokenId"
}
catch {
    # Mirror claude_teams_usage_sync's own fetch_status semantics: a 401
    # here means this member's local accessToken itself is expired or
    # revoked ("auth_error" / "期限切れ"), not that a usage limit was hit.
    # Any other failure (network, 5xx, etc.) is a generic "error" and does
    # NOT mean the token is invalid.
    $statusCode = $null
    if ($_.Exception.Response) {
        $statusCode = [int]$_.Exception.Response.StatusCode
    }

    if ($statusCode -eq 401) {
        $fetchStatus = 'auth_error'
        # This 401 is against %USERPROFILE%\.claude\.credentials.json's
        # accessToken (used only for the /api/oauth/usage lookup below),
        # NOT the chat-side token registered at first run -- that one was
        # pasted from 'claude setup-token' output and is stored server-side
        # only, unaffected by this machine's day-to-day CLI login state.
        # Re-running 'claude setup-token' would NOT fix this: per
        # Anthropic's docs it only prints a token and never writes
        # .credentials.json. Run 'claude login' (or just use the CLI
        # normally) to refresh it instead.
        $errorMessage = '401 Unauthorized calling /api/oauth/usage with this machine''s .claude\.credentials.json accessToken (expired or revoked) -- run claude login on this machine to refresh it. This is unrelated to the chat-side token registered at first run, which is unaffected.'
        # NOTE: the Go .exe that supersedes this script (see
        # scripts/claude_teams_member_agent_go) deliberately no longer
        # shows this particular popup. The local login it reads lapses
        # within hours of not using Claude Code and is never refreshed
        # here, so for a member not currently using Claude Code this
        # 401 is the normal state, and the dialog fired on every
        # scheduled run with nothing for them to act on. Left in place
        # here only because this script is retained as the historical
        # 1:1 reference for the rewrite, not because anyone should run
        # it -- see docs/CLAUDE_TEAMS_OAUTH.md, "Why an expired local
        # login is silent".
        Show-UserNotification -Title 'Claude Teams: usage tracking needs re-login' `
            -Message "Your Claude Code login has expired, so 5-hour/7-day usage tracking stopped working (chat itself is unaffected). Run 'claude login' in a terminal to fix it -- tracking resumes automatically on the next scheduled run, or re-run claude_teams_member_agent.bat now to confirm it right away."
    }
    else {
        $fetchStatus = 'error'
        $errorMessage = "HTTP $statusCode`: $($_.Exception.Message)"
    }

    try {
        Invoke-UsageIngest -TokenId $tokenId -IngestSecret $ingestSecret -Body @{
            fetch_status         = $fetchStatus
            fetch_error_message  = $errorMessage
            sampled_at_ms        = $sampledAtMs
        }
    }
    catch {
        Write-Host "ERROR: also failed to report the failure itself to bedrock-chat: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }

    Write-Host "WARNING: $errorMessage" -ForegroundColor Yellow
    exit 0
}

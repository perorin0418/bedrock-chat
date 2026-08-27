#requires -Version 5.1
<#
.SYNOPSIS
    Obtain a full-scope, long-lived Claude Code OAuth token (including the
    `user:profile` scope needed by Anthropic's `/api/oauth/usage` endpoint)
    for registration in the bedrock-chat "Claude Teams Tokens" admin page.

.DESCRIPTION
    `claude setup-token` (Anthropic's official CLI command) only requests
    the `user:inference` scope. That's sufficient for chat/inference calls
    but is *not* sufficient for the `/api/oauth/usage` endpoint
    bedrock-chat's `claude_teams_usage_sync` Lambda polls hourly to show
    5-hour/7-day usage limits and detect token expiry -- that endpoint
    requires `user:profile` and returns `403 permission_error` without it.

    This script drives the same Authorization Code + PKCE OAuth flow
    Claude Code itself uses (`claude.ai/oauth/authorize` ->
    `console.anthropic.com/v1/oauth/token`), but explicitly requests the
    broader `org:create_api_key user:profile user:inference` scope set and
    an extended `expires_in` lifetime, matching the pattern documented at
    https://gist.github.com/ben-vargas/c7c7cbfebbb47278f45feca9cef309d1
    (there is no first-party Anthropic documentation for this -- endpoint
    details/scope behavior may change without notice).

    Pure PowerShell 5.1 (built into every supported Windows version) --
    no Python, Node, or any other runtime install required.

.EXAMPLE
    Right-click get_full_scope_oauth_token.ps1 > "Run with PowerShell",
    or from a PowerShell prompt:
        .\get_full_scope_oauth_token.ps1

    Then follow the printed instructions: open the URL, authorize, and
    paste the code back. The resulting access token is printed once at
    the end -- copy it directly into the admin page's "OAuth Token" field.
    It is never written to disk by this script.
#>

$ErrorActionPreference = 'Stop'

$ClientId = if ($env:ANTHROPIC_CLIENT_ID) { $env:ANTHROPIC_CLIENT_ID } else { '9d1c250a-e61b-44d9-88ed-5944d1962f5e' }
$AuthorizeUrl = 'https://claude.ai/oauth/authorize'
$TokenUrl = 'https://console.anthropic.com/v1/oauth/token'
$RedirectUri = 'https://console.anthropic.com/oauth/code/callback'

# `user:profile` is the scope bedrock-chat's usage-history sync needs;
# `user:inference` is what chat/inference calls need (already covered by
# `claude setup-token`); `org:create_api_key` is included because it's
# part of the scope set Claude Code's own full-scope login requests, and
# asking for less than that set has been observed to make some servers
# reject the request outright.
$Scopes = 'org:create_api_key user:profile user:inference'

# One year, matching Claude Code's own long-lived `setup-token` lifetime.
# The server may cap or ignore this and return a shorter validity.
$DefaultExpiresInSeconds = 31536000

# console.anthropic.com sits behind Cloudflare, which rejects requests
# with no / a non-browser User-Agent outright with a generic
# "403 error code: 1010" (bad browser signature) -- before the request
# ever reaches Anthropic's own API logic. A standard browser User-Agent
# is enough to pass; the request itself doesn't need to (and isn't trying
# to) impersonate the Claude Code CLI's own client identity.
$BrowserUserAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

function New-PkcePair {
    $verifierBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($verifierBytes)
    $verifier = [Convert]::ToBase64String($verifierBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $challengeBytes = $sha256.ComputeHash([System.Text.Encoding]::ASCII.GetBytes($verifier))
    $challenge = [Convert]::ToBase64String($challengeBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

    return @{ Verifier = $verifier; Challenge = $challenge }
}

function Build-AuthorizeUrl {
    param(
        [Parameter(Mandatory)][string]$Challenge,
        [Parameter(Mandatory)][string]$State
    )
    $query = @(
        'code=true'
        "client_id=$([uri]::EscapeDataString($ClientId))"
        'response_type=code'
        "redirect_uri=$([uri]::EscapeDataString($RedirectUri))"
        "scope=$([uri]::EscapeDataString($Scopes))"
        "code_challenge=$([uri]::EscapeDataString($Challenge))"
        'code_challenge_method=S256'
        "state=$([uri]::EscapeDataString($State))"
    ) -join '&'
    return "$AuthorizeUrl`?$query"
}

function Invoke-TokenExchange {
    param(
        [Parameter(Mandatory)][string]$Code,
        [Parameter(Mandatory)][string]$State,
        [Parameter(Mandatory)][string]$Verifier,
        [Parameter(Mandatory)][int]$ExpiresIn
    )

    $bodyObject = [ordered]@{
        grant_type    = 'authorization_code'
        code          = $Code
        state         = $State
        client_id     = $ClientId
        redirect_uri  = $RedirectUri
        code_verifier = $Verifier
        expires_in    = $ExpiresIn
    }
    $bodyJson = $bodyObject | ConvertTo-Json -Compress

    try {
        return Invoke-RestMethod -Uri $TokenUrl -Method Post `
            -ContentType 'application/json' `
            -UserAgent $BrowserUserAgent `
            -Body $bodyJson
    }
    catch {
        $statusCode = $null
        $errorBody = $null
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
            try {
                $stream = $_.Exception.Response.GetResponseStream()
                $reader = New-Object System.IO.StreamReader($stream)
                $errorBody = $reader.ReadToEnd()
            }
            catch {
                $errorBody = '(could not read response body)'
            }
        }
        throw "Token exchange failed: HTTP $statusCode`n$errorBody"
    }
}

# --- Main ---

Write-Host ''
Write-Host 'Obtain a full-scope Claude Code OAuth token for bedrock-chat' -ForegroundColor Cyan
Write-Host '(includes the user:profile scope needed for 5h/7d usage-limit tracking)'
Write-Host ('=' * 72)
Write-Host ''

$pkce = New-PkcePair
$state = $pkce.Verifier
$authorizeUrl = Build-AuthorizeUrl -Challenge $pkce.Challenge -State $state

Write-Host 'Step 1: Open this URL in your browser and authorize:' -ForegroundColor Yellow
Write-Host ''
Write-Host $authorizeUrl
Write-Host ''
Write-Host 'Step 2: After authorizing, you will see an authorization code.'
Write-Host 'If it looks like "CODE#STATE", paste that whole string below.'
Write-Host "If it's just a plain code, paste that."
Write-Host ''

$rawInput = Read-Host 'Authorization code'
$rawInput = $rawInput.Trim()
if ([string]::IsNullOrEmpty($rawInput)) {
    Write-Host 'No code entered; aborting.' -ForegroundColor Red
    exit 1
}

if ($rawInput.Contains('#')) {
    $parts = $rawInput.Split('#', 2)
    $code = $parts[0]
    $returnedState = $parts[1]
}
else {
    $code = $rawInput
    $returnedState = $state
}

Write-Host ''
Write-Host 'Exchanging code for a full-scope OAuth token...'

try {
    $tokenResponse = Invoke-TokenExchange -Code $code -State $returnedState -Verifier $pkce.Verifier -ExpiresIn $DefaultExpiresInSeconds
}
catch {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

$accessToken = $tokenResponse.access_token
if ([string]::IsNullOrEmpty($accessToken)) {
    Write-Host ''
    Write-Host "ERROR: no access_token in response: $($tokenResponse | ConvertTo-Json -Compress)" -ForegroundColor Red
    exit 1
}

$grantedScope = if ($tokenResponse.scope) { $tokenResponse.scope } else { '(not reported)' }
$expiresIn = if ($tokenResponse.expires_in) { $tokenResponse.expires_in } else { '(not reported)' }

Write-Host ''
Write-Host ('=' * 72)
Write-Host 'SUCCESS. Full-scope OAuth token obtained:' -ForegroundColor Green
Write-Host ''
Write-Host $accessToken
Write-Host ''
Write-Host "Granted scope: $grantedScope"
Write-Host "expires_in (seconds, as reported by server): $expiresIn"
Write-Host ''
Write-Host 'Copy the token above into bedrock-chat''s admin "Claude Teams Tokens"'
Write-Host 'page, "OAuth Token" field. This script does not write it to any file.'

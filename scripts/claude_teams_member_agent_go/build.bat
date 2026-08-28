@echo off
REM Builds claude_teams_member_agent.exe with the org-wide API Endpoint
REM and Registration Secret baked in via -ldflags (see BUILD.md for
REM details on why baking these in, and their trust model).
REM
REM === ADMIN: you can pre-fill these two values here to skip the ===
REM === prompts below, or just leave them as placeholders and     ===
REM === answer the prompts when this file runs.                   ===
setlocal
set API_ENDPOINT=PASTE_API_ENDPOINT_HERE
set REGISTRATION_SECRET=PASTE_REGISTRATION_SECRET_HERE
REM === END ADMIN SECTION ===

REM NOTE: `set /p` is deliberately kept OUTSIDE any parenthesized
REM if-block below. cmd.exe pre-scans an entire "if (...)" block to
REM find its closing paren before executing it, and that pre-scan can
REM consume/misalign stdin out from under a `set /p` inside the same
REM block (a known cmd.exe fragility, not specific to this script) --
REM producing corrupted input or a spurious "The syntax of the command
REM is incorrect." goto/label-based flow avoids that entirely.

if not "%API_ENDPOINT%"=="PASTE_API_ENDPOINT_HERE" goto check_secret
echo API_ENDPOINT is not set yet.
echo Enter the bedrock-chat Backend API URL (the API Gateway one, e.g.
echo https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com -- NOT the
echo CloudFront/frontend URL).
set /p API_ENDPOINT="API Endpoint: "

:check_secret
if "%API_ENDPOINT%"=="" goto no_endpoint
if "%API_ENDPOINT%"=="PASTE_API_ENDPOINT_HERE" goto no_endpoint

if not "%REGISTRATION_SECRET%"=="PASTE_REGISTRATION_SECRET_HERE" goto check_secret_done
echo.
echo REGISTRATION_SECRET is not set yet.
echo Enter the value from the bedrock-chat admin page (Claude Teams
echo Tokens ^> Registration Secret).
set /p REGISTRATION_SECRET="Registration Secret: "

:check_secret_done
if "%REGISTRATION_SECRET%"=="" goto no_secret
if "%REGISTRATION_SECRET%"=="PASTE_REGISTRATION_SECRET_HERE" goto no_secret

goto have_values

:no_endpoint
echo ERROR: no API Endpoint entered. Re-run this script and enter one.
exit /b 1

:no_secret
echo ERROR: no Registration Secret entered. Re-run this script and enter one.
exit /b 1

:have_values
where go >nul 2>nul
if errorlevel 1 (
    echo ERROR: 'go' was not found on PATH. Install Go 1.23+ from
    echo https://go.dev/dl/ and re-run this script.
    exit /b 1
)

cd /d "%~dp0"

echo Running 'go mod tidy'...
go mod tidy
if errorlevel 1 (
    echo ERROR: 'go mod tidy' failed. See output above.
    exit /b 1
)

echo Building claude_teams_member_agent.exe for Windows/amd64...
set GOOS=windows
set GOARCH=amd64
go build -ldflags "-X main.defaultAPIEndpoint=%API_ENDPOINT% -X main.defaultRegistrationSecret=%REGISTRATION_SECRET%" -o claude_teams_member_agent.exe .
if errorlevel 1 (
    echo ERROR: build failed. See output above.
    exit /b 1
)

echo.
echo SUCCESS: claude_teams_member_agent.exe built in %~dp0
echo Distribute this one file to every member. They just double-click it.
echo.
pause

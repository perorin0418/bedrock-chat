@echo off
REM Double-click this file to run claude_teams_member_agent.ps1.
REM
REM Windows normally opens .ps1 files in Notepad when double-clicked
REM (a safety default), so this .bat wrapper launches PowerShell directly
REM instead. -ExecutionPolicy Bypass applies only to this one process and
REM does not change any system-wide PowerShell settings.
REM
REM === ADMIN: edit these two lines before distributing this file ===
REM Both values are org-wide (identical for every member) -- fill them
REM in once here, then hand this already-edited .bat (plus
REM claude_teams_member_agent.ps1, which it calls) to each member.
REM Members should not need to edit anything themselves.
setlocal
set REGISTRATION_SECRET=PASTE_REGISTRATION_SECRET_HERE
set API_ENDPOINT=PASTE_API_ENDPOINT_HERE
REM === END ADMIN SECTION -- members: just double-click this file ===
REM
REM A browser window will open once for 'claude setup-token' approval
REM (skipped if already logged in), and the script registers its own
REM hourly Task Scheduler entry before it exits -- nothing else for the
REM member to configure.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0claude_teams_member_agent.ps1" -RegistrationSecret "%REGISTRATION_SECRET%" -ApiEndpoint "%API_ENDPOINT%"
echo.
pause

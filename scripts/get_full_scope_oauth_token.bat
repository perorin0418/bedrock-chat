@echo off
REM Double-click this file to run get_full_scope_oauth_token.ps1.
REM
REM Windows normally opens .ps1 files in Notepad when double-clicked
REM (a safety default), so this .bat wrapper launches PowerShell directly
REM instead. -ExecutionPolicy Bypass applies only to this one process and
REM does not change any system-wide PowerShell settings.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_full_scope_oauth_token.ps1"
echo.
pause

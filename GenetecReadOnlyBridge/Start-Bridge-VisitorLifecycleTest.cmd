@echo off
setlocal
rem One-click, non-interactive launcher for supervised synthetic visitor lifecycle testing.
rem This enables two guarded scopes; use only with synthetic visits on an approved NON-PRODUCTION Directory.
rem Request confirmations, strict booking/event ID allowlists, version/licence checks and SDK privileges remain enforced.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Bridge.ps1" -EnableVisitorSync -EnableVisitorLifecycleSync
exit /b %ERRORLEVEL%

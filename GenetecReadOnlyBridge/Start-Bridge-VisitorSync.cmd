@echo off
setlocal
rem One-click, non-interactive launcher for the guarded native Visitor create/update scope.
rem Use only against an approved NON-PRODUCTION Genetec environment with synthetic VMS-* bookings.
rem Request-level confirmation, version/licence checks, and SDK privileges remain enforced.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Bridge.ps1" -EnableVisitorSync
exit /b %ERRORLEVEL%

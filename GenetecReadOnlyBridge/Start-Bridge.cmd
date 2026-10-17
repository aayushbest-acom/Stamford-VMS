@echo off
setlocal
rem Prompt-free Genetec bridge launcher. Defaults to read-only.
rem Optional explicit switches: -EnableVisitorSync, -EnableVisitorLifecycleSync,
rem -EnableTestCardholderWrites. Use write scopes only on approved non-production
rem Genetec with synthetic records; bridge request-level guards remain enabled.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Bridge.ps1" %*
exit /b %ERRORLEVEL%

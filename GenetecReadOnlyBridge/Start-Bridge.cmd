@echo off
setlocal
rem Prompt-free Genetec bridge launcher. Defaults to read-only.
rem Optional explicit switches: -EnableVisitorSync, -EnableVisitorLifecycleSync,
rem -EnableTestCardholderWrites. Face/photo lookup requires three separate explicit
rem policy acknowledgement switches and must only be used after written approval.
rem All switches become bridge command-line options, not feature environment variables.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Bridge.ps1" %*
exit /b %ERRORLEVEL%

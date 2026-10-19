@echo off
setlocal
cd /d "%~dp0"
echo Starting Stamford VMS prototype - BUILD 5.5.0
echo UI includes visitor/group workflows, attendance kiosk, visitor types, reports/exports, local accounts and feature permissions.
echo First launch: create the one-time Administrator account; later launches require sign-in.
echo URL: http://127.0.0.1:8080
echo Persistence: local SQLite service. Authenticated state is kept in the local service, not browser local storage.
echo QR scanner: browser native detection or local OpenCV frame decoder.
echo SAFETY: prototype only; loopback-only; local accounts and server-side feature permissions are enabled; use synthetic data only.
echo Kiosk face attendance: mock by default; live recognition requires written school approval, person-specific consent, approved Genetec linkage and an on-host UAT.
echo Kiosk attendance records are local only; this flow does not write attendance events to Genetec.
echo Genetec Cardholder image import: disabled by default; use only with written approval and consent records.
echo Launch folder: %CD%
echo.
rem Refuse to attach to an old VMS instance that could mask this build's UI changes.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$client = New-Object System.Net.Sockets.TcpClient; try { $client.Connect('127.0.0.1',8080); exit 19 } catch { exit 0 } finally { $client.Dispose() }"
set "PORT_CHECK=%ERRORLEVEL%"
if "%PORT_CHECK%"=="19" goto PORT_IN_USE
if not "%PORT_CHECK%"=="0" goto PORT_CHECK_FAILED
rem Install local QR decoding support only when the installed Python cannot import cv2.
python -c "import cv2" >nul 2>&1
if errorlevel 1 (
  echo Browser QR detection is missing/unsupported; preparing local OpenCV QR decoder.
  python -m pip install --disable-pip-version-check --user -r requirements-scanner.txt
  if errorlevel 1 echo WARNING: QR scanning fallback could not be installed. Manual token entry remains available.
)
echo Starting the local Genetec connection automatically when configured...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0GenetecReadOnlyBridge\Start-Bridge-Automatically.ps1"
if errorlevel 1 echo Warning: Genetec auto-start did not complete. The VMS will still open; check the local bridge log.
python service\vms_service.py --ui-dir "%~dp0ui" --host 127.0.0.1 --port 8080
if errorlevel 1 (
  echo.
  echo The local prototype service failed to start. Confirm Python 3.10+ is installed and port 8080 is available.
  pause
)
goto LAUNCH_END

:PORT_IN_USE
echo ERROR: Port 8080 is already in use. A previous VMS instance is likely still running.
echo Close the old VMS console, then launch Start-Demo.cmd from this Milestone 5.5.0 folder.
echo The launcher stopped here so the older UI cannot silently appear to be the updated build.
timeout /t 15 /nobreak >nul
exit /b 2

:PORT_CHECK_FAILED
echo ERROR: Could not check whether port 8080 is free. Verify PowerShell is available, then retry.
timeout /t 15 /nobreak >nul
exit /b 2

:LAUNCH_END
endlocal

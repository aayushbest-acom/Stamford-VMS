# Current note — Milestone 5.1.2 QR camera compatibility

The current scanner uses native `BarcodeDetector` where available and falls back to a loopback OpenCV QR decoder when the browser API is unavailable. This was added after target-browser evidence showed Edge lacked `BarcodeDetector`. See `VALIDATION-REPORT-MILESTONE5.1.2.md` and `MILESTONE5.1.2-CAMERA-SCANNER-FALLBACK.md` for current test results and Windows host checks. Older sections below record previous milestone evidence and should be read historically.

---

# Stamford VMS — Milestone 5 Validation Report

**Date:** 17 October 2026  
**Scope:** Automatic Genetec bridge launcher wiring, startup status probe, explicit visitor time selection, laptop-camera QR scanning source contracts, existing transactional visitor API, Unicode visit-window normalization, and Genetec bridge source contracts.  
**Disposition:** Local automated/static checks pass. Windows, camera, and live Genetec runtime verification remains pending.

## Results

| Check | Result | Notes |
|---|---|---|
| Python automated tests | **PASS (29/29)** | Includes API transitions, secure QR pass lifecycle, revision checks, source contracts for auto-start, explicit time fields and camera scanning. |
| Python syntax/bytecode compilation | **PASS** | `python -m py_compile service/*.py tests/*.py`. |
| Inline UI JavaScript syntax | **PASS** | Extracted inline script and checked with Node.js `--check`. |
| Visit-window normalization | **PASS (5 valid, 3 invalid)** | Unicode dash/space variants canonicalize; invalid clock values and malformed windows reject. |
| UI requirement contracts | **PASS** | Confirms blank required start/end time fields, camera API and native QR detector wiring, no visible Day 1/Day 2 labels, and startup Genetec probe. |
| Genetec auto-start source contract | **PASS (static only)** | `Start-Demo.cmd` calls the background launcher; it reuses an existing listener and uses the saved local DPAPI profile. |
| Requirements traceability | **PASS (110 unique IDs)** | Requirement IDs retained. |
| Windows PowerShell / DPAPI auto-start | **NOT RUN** | Must be executed on the user's Windows host. |
| Live webcam QR detection | **NOT RUN** | Native `BarcodeDetector` and real camera permissions require current Edge/Chrome on the target laptop. Manual entry remains the fallback. |
| .NET bridge build | **NOT RUN** | This environment does not have the target Genetec SDK / Windows framework. |
| Live Genetec round-trip | **NOT RUN** | Requires target Windows host and licensed Security Center 5.11 test Directory. |
| Full browser/device UAT | **NOT RUN** | Needs the target browser, webcam, printer, network, and approved test setup. |

## Reproduce local checks

```powershell
python -m unittest discover -s tests -v
python -m py_compile service/*.py tests/*.py
```

Extract the inline JavaScript from `ui/index.html` and run `node --check` where Node.js is installed.

## Target-host checks required

1. Run `Start-Demo.cmd` in the normal Windows account with the saved DPAPI profile. Confirm the Genetec connection appears on the overview without opening diagnostics, and review `%LOCALAPPDATA%\StamfordVMS\Logs` if unavailable.
2. Open the kiosk on current Edge/Chrome, allow camera permission, and test scanning a QR rendered by the VMS itself. Confirm an unknown QR is rejected and a valid QR runs the existing server check-in/out transition once.
3. Create an invitation with two explicitly selected times and confirm the displayed/saved window and native Genetec appointment schedule agree.
4. On a non-production Directory using synthetic bookings only, validate invite sync and check-in/check-out lifecycle sync with native Genetec read-back. Check existing state before retrying an operation with an ambiguous outcome.

## Safety boundary

The application is still a loopback prototype with no enterprise authentication/RBAC. Its local UI remains synthetic-data-only. The local bridge is still required as the Genetec SDK adapter, although the launcher starts it in the background automatically. No production readiness or compliance with the entire SRS is implied by these checks.

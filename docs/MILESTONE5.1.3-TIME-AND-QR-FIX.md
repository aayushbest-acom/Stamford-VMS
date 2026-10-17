# Stamford VMS — Milestone 5.1.3

## Fixes

1. **Minute-level time selection:** invitation Start time and End time inputs use `step=60`, so users can select any valid minute rather than only 15-minute increments. The modal labels the school timezone and disallows an end time equal to or earlier than the start.
2. **School-local calendar semantics:** invitation date defaults and minimum dates use Asia/Singapore calendar dates instead of the laptop’s timezone. The backend validates and expires passes against the same Asia/Singapore (UTC+08:00) school timezone.
3. **QR check-in rejection diagnostics:** QR validation responses now include server school-local time, visit start/end and permitted check-in bounds. Rejection toasts display these values so a time-related failure explains the comparison it made.
4. **Visit-clock consistency:** visit creation’s past/future date validation, QR expiry, and check-in-window checks use the same school timezone.
5. **School-local displayed arrival/departure:** secure QR workflow displays recorded arrival and departure times in Asia/Singapore.

## Verification in this package

- Automated regression suite: **38/38 passed**.
- Python compilation: passed.
- Browser inline JavaScript syntax: passed using Node.js `--check`.
- Added regression coverage for arbitrary-minute visit windows, check-in during a booked school-local time range, and returned timing diagnostics.

## Retest on target Windows host

1. Stop the currently running VMS with Ctrl+C.
2. Extract this ZIP into a new folder and run `Start-Demo.cmd`. Confirm `BUILD 5.1.3` / `UI BUILD 5.1.3`.
3. Create a synthetic invitation for today or tomorrow with a non-quarter-hour window such as `10:07`–`10:38`.
4. Generate/show its secure QR code and scan it during that visit window.
5. If it is rejected, the toast now reports school time and allowed bounds. Save that exact message; it will show whether the host clock or visit value differs from school time.

## Known boundary

This remains a loopback-only prototype without application authentication/RBAC. Use synthetic data only. A real Edge webcam scan and the native Genetec lifecycle must still be verified on the target Windows/Genetec host; local tests do not assert that those external runtime checks have passed.

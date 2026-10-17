# Stamford VMS — Milestone 5.1.3 validation report

## Automated results

- `python -m unittest discover -s tests -v`: **38 tests passed**.
- `python -m py_compile service/domain.py service/vms_service.py service/qr_code.py service/qr_frame_decoder.py`: passed.
- Extracted inline JavaScript from `ui/index.html` and ran `node --check`: passed.
- New regression tests cover minute-level times, a check-in inside a booked Asia/Singapore visit window, and diagnostics with exact school-local bounds.

## Not run here

- Real Microsoft Edge camera acquisition/decode on the user's laptop.
- Rebuild and live lifecycle round-trip against the installed Genetec Security Center 5.11 SDK.
- A true target-host test where Windows timezone differs from Asia/Singapore.

The release therefore validates local code behaviour, not full production acceptance.

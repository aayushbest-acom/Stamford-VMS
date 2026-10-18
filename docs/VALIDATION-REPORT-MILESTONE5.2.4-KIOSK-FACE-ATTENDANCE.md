# Validation Report — Build 5.2.4 Kiosk Face Attendance

## Change summary

Added a category-based School Attendance kiosk flow for Student, Staff and Contractor; explicit match review/confirmation; session-local Check In/Check Out records; live/mock mode labeling; duplicate-event and checkout-before-check-in guards; and allow-listed Genetec profile metadata (first/last name, External ID and Cardholder Groups where returned). The read-only Genetec Cardholder-photo response now attempts to return Cardholder group names where supported by the installed SDK.

## Validation commands

Run these from the repository root on the dev host:

```powershell
python -m py_compile service/face_attendance.py service/vms_service.py
python -m unittest discover -s tests -v
```

Extract inline UI JavaScript and syntax-check it with Node:

```powershell
python -c "import re; from pathlib import Path; s=Path('ui/index.html').read_text(encoding='utf-8'); Path('inline-ui-check.js').write_text('\n'.join(re.findall(r'<script>(.*?)</script>', s, re.S)), encoding='utf-8')"
node --check inline-ui-check.js
Remove-Item inline-ui-check.js
```

## Results from this implementation pass

- Python compilation of `service/face_attendance.py` and `service/vms_service.py`: passed.
- Full Python automated regression suite: **90/90 passed** after adding kiosk flow and profile metadata tests.
- Inline UI JavaScript `node --check`: passed.
- The Genetec bridge C# project has **not** been compiled in this environment because the exact installed Security Center SDK/runtime belongs to the target Windows host. The added `cardholderGroups` property must pass that host build and a read-only live query before acceptance.

## Acceptance boundaries

- Automated tests verify backend category validation/scope, mock-only labeling, confirmation gates, session state, duplicate event handling and no-checkout-before-checkin.
- Mock results are synthetic and do not represent biometric matching.
- Live mode still uses the approved local face-enrollment gallery; it does not conduct unrestricted matching against all Genetec portraits.
- Genetec integration remains read-only for portrait retrieval. Attendance records created by the new kiosk path are local prototype ledger entries only (`writtenToGenetec: false`).
- This environment cannot verify a live Windows webcam, production printer, SMS provider or actual Genetec SDK/installation. The bridge code change must be rebuilt and smoke-tested on the supported Windows/Genetec host because SDK compatibility is installation-specific.
- The existing app authentication/RBAC blocker remains; use synthetic data and loopback access only until security work and policy approvals are accepted.

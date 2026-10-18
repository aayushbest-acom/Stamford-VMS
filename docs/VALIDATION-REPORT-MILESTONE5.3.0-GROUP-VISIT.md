# Validation Report — Build 5.3.0 Group Visits

## Scope checked

Added booking-owned group visit state, mixed-person roster entry, opaque group QR token/rotation, live webcam capture API, per-scan image metadata and timestamp, optional protected photo retention, roster-scoped face candidate matching, annotated photo preview, explicit human batch confirmation, per-member attendance state, full-roster reconciliation and UI navigation through Group Visits.

## Automated checks

Run from the repository root:

```powershell
python -m py_compile service/group_visits.py service/face_attendance.py service/vms_service.py
python -m unittest discover -s tests -v
python -c "from pathlib import Path; s=Path('ui/index.html').read_text(encoding='utf-8'); Path('inline-ui-check.js').write_text(s[s.index('<script>')+8:s.rfind('</script>')],encoding='utf-8')"
node --check inline-ui-check.js
Remove-Item inline-ui-check.js
```

Final validation for this patch set:

- `python -m py_compile service/group_visits.py service/face_attendance.py service/vms_service.py tests/test_group_visits.py tests/test_repository_contracts.py` — passed.
- Inline browser JavaScript extraction followed by `node --check` — passed.
- `python -m unittest discover -s tests -v` — **111 tests passed, 0 failures**.
- `git diff --check` — passed; Git emitted only the expected line-ending notice for `Start-Demo.cmd` (LF will be normalised to CRLF by Git on Windows).

These checks validate the source patch and automated local contracts. They do not replace target-Windows webcam, DPAPI, OpenCV-contrib, browser, or Genetec SDK/Directory acceptance. Re-run the checks after applying the patches to the intended working tree.

## Known acceptance boundaries

- Default `STAMFORD_GROUP_VISIT_MODE=mock`: roster candidate names and boxes are scripted. The captured image is not used for identity recognition; the UI explicitly labels the result.
- Live matching requires the separate group-identity approval and existing face-attendance/policy/Windows DPAPI/OpenCV gates. It is roster-scoped to active individual profiles linked to this group.
- Group photos are never enrolled as face templates. Raw group photos are not retained by default. Optional retention requires an explicit gate plus approved expiry and DPAPI-protected storage on Windows.
- The current 100-person roster cap is a prototype guardrail, not a final capacity requirement. Automatic count comparison and large-group sequential subgroup handling remain open.
- No enterprise application authentication/RBAC is present; loopback and synthetic data only.
- This Linux/development execution cannot prove a real webcam, Windows DPAPI, OpenCV-contrib recognition quality or the installed Genetec SDK/server. Those are target-host/pilot tests, not unit-test claims.

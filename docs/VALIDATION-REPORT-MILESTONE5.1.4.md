# Stamford VMS — Milestone 5.1.4 validation report

- Automated regression suite: **40/40 passed**.
- Python source compilation: passed.
- Inline JavaScript syntax (`node --check`): pending final package check.
- Visit invitation defaults to today's school-local date: source-contract test added.
- Schedule amendment API: tested date/time update, token preservation, updated expiry, and check-in inside the amended school-local window.
- Schedule amendment after check-in: correctly rejected.
- Out-of-window QR failure includes booked visit date/window and permitted start/end diagnostics.
- Requirement traceability: 110 unique SRS IDs retained.

## Still requires target-host validation

- Browser test on the user's Edge installation.
- Confirm the previous 18 October invitation can be amended to the intended date/time using the existing QR.
- If already linked to Genetec, repeat the guarded native Visitor sync and verify schedule read-back on the installed Security Center 5.11 SDK host.
- Full Windows bridge / camera / end-to-end UAT remains external to this local test environment.

This remains a loopback-only prototype without application authentication/RBAC. Continue using synthetic test data only.

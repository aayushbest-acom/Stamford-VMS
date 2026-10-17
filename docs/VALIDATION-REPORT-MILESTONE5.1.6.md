# Validation report — Stamford VMS Build 5.1.6

## Change
The visit-details UI now handles the case shown in the operator screenshot: a VMS visit is locally `Checked out`, the original Genetec check-in lifecycle event remains `Pending`/`Failed`, and the checkout event has failed because the bridge ledger has no read-back-verified check-in. Instead of offering checkout first, the UI selects the original check-in event as the prerequisite action. Once that event is verified as `Synced`, reopening details selects the original checkout event.

The UI prevents a checkout request until the check-in record is `Synced`. For this narrow recovery path, it permits the original historical check-in event only if both original check-in and checkout records exist, their UTC timestamps are chronological, and the check-in is within the bridge's 30-minute event freshness window. It preserves original event IDs/timestamps and does not fabricate a new event. If checks fail, the UI directs operators to read-only inspection/reconciliation instead.

The stale button-state update in `finally` was removed. After a successful check-in, that code could otherwise disable the newly rendered checkout button based on the completed check-in record's state.

## Checks run
- **42/42 automated Python tests passed.**
- `python -m compileall -q service tests` passed.
- Node.js `--check` passed for the inline script extracted from `ui/index.html`.
- Requirement traceability retains **110 unique requirement IDs**.
- Build identifiers aligned to `5.1.6` across `VERSION.txt`, UI metadata, service health/User-Agent, Genetec bridge and automatic bridge restart helper.

## Not verified in this environment
- C# bridge build against the installed Windows Genetec Security Center 5.11 SDK.
- PowerShell auto-start/bridge replacement on the target Windows host.
- Live Genetec CheckIn mutation, independent read-back, and subsequent Checkout/archive read-back for this recovery path.
- Browser UI acceptance on the operator's workstation.

## Target-host acceptance steps
1. Stop the previous VMS/bridge process; extract Build 5.1.6 into a fresh folder; launch `Start-Demo.cmd` and verify both UI and bridge say `5.1.6`.
2. Use only a synthetic visit in an approved non-production Directory. Open Details for the visit that is locally checked out with CheckIn pending and CheckOut failed.
3. Confirm the next action is **Sync prerequisite Genetec check-in first**. Verify the stored check-in timestamp is recent (within 30 minutes) and earlier than checkout. Do not proceed if timestamps or live Genetec state are ambiguous.
4. Submit the original CheckIn event and require `readBackVerified=true`, correct mapped Visitor GUID and active activation/check-in timestamp matching the original event.
5. Reopen Details. Verify the action now targets the original CheckOut event. Submit it and require independent archived `CheckedOut` read-back plus canonical check-in timestamp consistency.
6. Inspect `GET /api/genetec/visitor-lifecycle-state?bookingId=<synthetic booking>` and retain redacted evidence. If a mutation may have succeeded but read-back failed, do not blindly repeat it—inspect first.

## Safety boundary
The bridge's check-in-before-checkout ledger guard remains unchanged. Checkout remains blocked if the original check-in is missing or not read-back verified. Visits older than the bridge's 30-minute new-event window need explicit read-only investigation and operator reconciliation, not backdating or a synthetic replacement timestamp. Prototype authentication/RBAC remains unconfigured; use synthetic data only.

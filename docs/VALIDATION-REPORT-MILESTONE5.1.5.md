# Validation report — Stamford VMS Build 5.1.5

## Change
The bridge invitation-upsert path no longer requires the scheduled start to be more than one minute in the future. A synthetic booking that remains `Invited` may be synced during its still-open scheduled visit window. Invitation upsert writes planned `VisitDate`/`Departure` and sets native activation to `Never`; it does not record attendance.

## Checks run in this environment
- **41/41 Python regression tests passed.**
- Python `compileall` passed for `service` and `tests`.
- Node.js `--check` passed for the inline JavaScript extracted from `ui/index.html`.
- Static repository contract confirms the obsolete `INVITED_ARRIVAL_NOT_FUTURE` guard is absent and the `VISIT_WINDOW_EXPIRED` rejection remains.
- The bridge health response now exposes the bridge build and executable path; auto-start detects an older/unversioned bridge and tries to replace it only when its process can be uniquely identified as `GenetecReadOnlyBridge.exe`. It does not stop unrelated processes.
- Requirement traceability matrix retains all 110 unique SRS requirement IDs.

## Not verified here
- C# bridge compilation against the target Windows Genetec Security Center 5.11 SDK.
- PowerShell execution/permissions and stale-process replacement on the Windows host.
- Live Directory read-back for a booking synced after its scheduled start while still before departure.
- Confirmation in Security Desk that the Visitor remains manually check-in eligible with `ActivationType=Never`.

## Required target-host test
In an approved non-production Genetec Directory, extract this build in a fresh folder and launch `Start-Demo.cmd`. Confirm the bridge log reports build `5.1.5`. Create a fresh synthetic booking for today's date with a start time already reached and a departure at least five minutes in the future. Confirm the VMS status is still `Invited` and no local lifecycle event exists. Sync invitation; then perform a read-only lookup and confirm exactly one native Visitor, matching planned VisitDate/Departure, `ActivationType=Never`, and no activation date. Confirm the Visitor remains manually check-in eligible in Security Desk. Separately confirm an expired appointment is still rejected. Do not retry ambiguous writes before a read-only lookup.

## Safety
The bridge remains loopback-only. Only synthetic bookings in `Invited` state are eligible for invitation upsert. Existing lifecycle events, ambiguous/activated native Visitor records, expired appointments, duplicate stable keys, unsupported Directory versions, missing Visitor Management licence and failed read-back continue to block or fail the operation. Actual check-in/checkout must go through the separate guarded lifecycle endpoint after the VMS records the event.

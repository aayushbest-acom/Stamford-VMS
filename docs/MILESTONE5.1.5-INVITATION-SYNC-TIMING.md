# Build 5.1.5 — invitation sync timing correction

## Why the previous bridge rejected it
The bridge had a hard guard requiring scheduled arrival to be more than one minute in the future. That check ran even when the VMS booking was still `Invited`, no check-in event had been recorded, and the scheduled visit window had not ended. This is why a valid same-day appointment could fail to synchronize after the start time.

## Correction
The bridge now permits an invited booking to create/update the native Visitor while the scheduled window remains open, even if its start time is current or has passed. The native Visitor is still written with the planned `VisitDate`/`Departure` and activation `Never`. Invitation sync is not attendance. The separate guarded lifecycle endpoint remains the only path for a real check-in/check-out event after the VMS records that event.

## Guardrails retained
- Only synthetic VMS IDs in `Invited` status are accepted.
- Bookings with any existing lifecycle event are rejected by invitation upsert.
- Existing native Visitor entities must still be unambiguously pre-registered; an activated or ambiguous record is not overwritten.
- Appointments whose scheduled departure has passed are rejected.
- Duplicate stable keys, missing Visitor Management licensing, unsupported Directory version and failed independent read-back remain fail-closed.

## Stale bridge protection
The bridge health endpoint now reports build `5.1.5` and its executable path. Auto-start detects an older or unversioned bridge and attempts to stop/rebuild it only when it can identify the running process as `GenetecReadOnlyBridge.exe`. If the process cannot be uniquely identified or stopped, it warns rather than killing an unrelated process.

## Validation boundary
41/41 Python regression tests passed, Python compilation passed, and JavaScript syntax passed. The C# bridge still must be rebuilt and verified against the installed Windows Genetec Security Center 5.11 SDK.

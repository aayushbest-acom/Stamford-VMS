# Day 2 integration contract: visitor check-in/check-out

**Status:** Guarded prototype endpoint and candidate SDK operation are implemented in the Day 2 worktree. Native Visitor upsert/idempotency is verified on the target Directory; check-in/check-out lifecycle round-trip remains unverified until the non-production lifecycle test passes.

## Goal

After a synthetic invited VMS booking has been synchronized to one native Genetec `Visitor`, record its actual arrival/check-in and checkout lifecycle with duplicate-safe retries and independently verifiable evidence.

## Non-negotiable design gate

The installed Security Center 5.11 SDK XML exposes `Visitor.Arrival` as the activation date/time, a read-only `Visitor.CheckinDate`, a dedicated `Visitor.Checkout()` method, separate `CheckInVisitors` and `CheckOutVisitors` privileges, and archived Visitor state/timestamps. Genetec's Security Center 5.12 User Guide says a visitor's activation date is the same as its check-in date and describes a distinct “check in” action. That later-version guide is context, not direct proof of behavior on Stamford's 5.11.3130.13 build; the guarded on-host read-back test remains authoritative. See the [Security Center SDK overview](https://developer.genetec.com/r/en-us/overview-of-the-security-center-sdk) and [Genetec guide for checking in visitors](https://techdocs.genetec.com/r/en-US/Security-Center-User-Guide-5.12/Checking-in-new-visitors).

Invited booking sync must first pre-register the Visitor: map planned arrival to `Visitor.VisitDate`, set activation to `Never` with `Visitor.Status.Activate(DateTime.MinValue)`, set planned departure/expiration, and read back `ActivationType=Never` with no `CheckinDate`. Only the subsequent candidate check-in event sets a specific activation at the recorded event time and sets `Visitor.Arrival`; it reports success only if a fresh SDK session reads back `CheckinDate` within the agreed timestamp tolerance. Checkout calls `Visitor.Checkout()` and requires an archived `CheckedOut` Visitor with check-in and checkout timestamps. This is a candidate mapping, not yet a verified mapping for Stamford's Directory; the guarded non-production end-to-end test must prove it. If read-back fails, the bridge must report failure and operator reconciliation is required. Never return success for a VMS-only status change as if Genetec confirmed it.

## Proposed endpoints

### `GET /api/genetec/capabilities`

A read-only endpoint that advertises supported and enabled features separately. For lifecycle sync, return fields equivalent to:

```json
{
  "visitorLifecycleSyncSupported": false,
  "visitorLifecycleSyncEnabled": false,
  "visitorLifecycleSyncScope": "disabled",
  "lifecycleOperationMappingVerified": false
}
```

`visitorLifecycleSyncSupported` indicates that the loaded SDK exposes the members required by the guarded candidate implementation. `visitorLifecycleSyncEnabled` indicates the explicit non-production startup opt-in. `lifecycleOperationMappingVerified` remains false until a successful check-in and checkout both pass independent native read-back. A startup flag alone must not imply that the runtime mapping has been validated.

### `POST /api/genetec/visitor-lifecycle`

Loopback-only, separately gated mutation endpoint. Proposed request:

```json
{
  "Confirmation": "SYNC SYNTHETIC STAMFORD VMS VISITOR LIFECYCLE",
  "BookingId": "VMS-SDKTEST-01",
  "EventId": "VMS-SDKTEST-01-CHECKIN-01",
  "EventType": "CheckIn",
  "EventAtUtc": "2030-01-15T01:00:00.0000000Z",
  "VmsStatus": "Inside"
}
```

`EventType` is exactly `CheckIn` or `CheckOut`; `VmsStatus` is exactly `Inside` or `Checked out`, respectively. Use UTC timestamps. The sample timestamp is illustrative only; test scripts should generate current timestamps and use synthetic test bookings.

## Required controls

- The bridge stays bound to `127.0.0.1:5190`; do not broaden the listener.
- Require a dedicated startup opt-in such as `ENABLE SYNTHETIC VISITOR LIFECYCLE SYNC FOR NON-PRODUCTION` plus the exact per-request confirmation above.
- Require a licensed Visitor Management feature, Directory version approved for the installed 5.11 SDK, an authenticated least-privilege account, and an existing native Visitor that maps unambiguously to the booking ID.
- Restrict the first live test to a designated `VMS-SDKTEST-*` synthetic booking. Never use student/staff visitor records for this smoke test.
- Do not create or change credentials, cardholder groups, escorts, access rights or physical access permissions.
- Do not treat the scheduled visit window as actual arrival/departure evidence. In SDK 5.11, `Visitor.Arrival` is the activation date/time; planned arrival must be stored in `Visitor.VisitDate`. Invited Visitors must remain at activation `Never` until the VMS records actual check-in.
- Do not report success unless the supported Genetec lifecycle operation completed and the result was independently read back or inspected via a supported query.

## Event idempotency and ordering

- `BookingId` points to the stable native Visitor created during the booking sync milestone.
- `EventId` is the idempotency key for one lifecycle event. Retrying the same event ID with the same payload must return the original result without applying another mutation.
- Reusing the same event ID with different `EventType`, timestamp or booking ID must fail with a conflict.
- Check-out must be rejected if the booking has no verified check-in event, unless Stamford approves a documented reconciliation route.
- A failure between SDK mutation and local response must be reconcilable using a supported read-back. Do not rely only on an in-memory set of seen event IDs for production-grade exactly-once behavior; the Day 2 prototype must document whether replay safety survives bridge restarts.
- Out-of-order or conflicting lifecycle events fail closed and create visible diagnostics; do not silently adjust event time or status.

## Proposed successful response

```json
{
  "success": true,
  "bookingId": "VMS-SDKTEST-01",
  "eventId": "VMS-SDKTEST-01-CHECKIN-01",
  "eventType": "CheckIn",
  "visitorGuid": "<actual Genetec GUID>",
  "operationId": "<correlation ID>",
  "readBackVerified": true,
  "alreadyApplied": false,
  "accessRightsChanged": false,
  "credentialChanged": false
}
```

The bridge now returns these fields from the guarded lifecycle endpoint. Actual GUIDs and success values must come from the SDK result/read-back; placeholders must never be emitted as real results.

## UI behavior

- VMS status may update locally only after the kiosk/guard action is validated according to the VMS state machine. The UI must separately display Genetec sync state: `Pending`, `Synced`, `Failed` or `Needs reconciliation`.
- If Genetec fails, retain the event as pending and expose a retry/reconciliation action. Do not revert or falsify the recorded kiosk interaction, and do not display `Synced` based only on a successful HTTP response without server-side verification.
- Use a stable event ID generated once per action and reuse it across retries.

## Day 2 exit criteria

1. SDK member availability has been inspected in the installed 5.11 SDK; the candidate operation mapping must be confirmed against a designated non-production Directory with independent read-back.
2. An invited booking is pre-registered with `ActivationType=Never` and no native `CheckinDate`; the scheduled start alone must not create a checked-in record.
3. Check-in event succeeds and is independently verified in Genetec.
4. An identical event retry is idempotent and returns the same Visitor GUID and prior operation evidence.
5. Same event ID with a different payload is rejected.
6. Check-out before check-in is rejected; check-out after verified check-in succeeds and is independently verified.
7. Duplicate checkout is idempotent.
8. Negative tests prove no credential/access-right changes, no write when disabled, no write for non-synthetic booking IDs, and no false success when read-back is unavailable.
9. The web UI shows actual per-event sync status and the audit trail includes booking ID, event ID, actor/source, timestamp, operation ID, outcome and error code without secret values.
10. The bridge performs a read-only Directory lookup before lifecycle mutation and fails closed on zero/multiple matches or a conflicting booking-to-GUID mapping.
11. Legacy/incompatible Visitor with native `CheckinDate` already set but no bridge event is blocked as `VISITOR_ALREADY_CHECKED_IN_UNTRACKED`; no automatic reset or new write occurs.


Capability reporting separates SDK member availability, explicit startup opt-in, and completion of a live check-in/check-out round-trip. A running bridge can report lifecycle SDK support and opt-in as true while `lifecycleOperationMappingVerified` remains false until the live end-to-end test passes. The lifecycle test harness is permitted to run only in the guarded non-production test mode and only after read-only preflight confirms exactly one Visitor with activation `Never` and no native `CheckinDate`.

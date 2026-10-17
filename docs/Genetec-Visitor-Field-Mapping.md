# Genetec Security Center 5.11 — VMS visitor mapping (prototype)

## Purpose and status

This document defines the deliberately narrow mapping used by the guarded native Visitor synchronisation prototype. It is not the final production data-ownership or access-policy specification. Run live writes only against an approved non-production Security Center environment and synthetic people.

## Preconditions

- The target Directory reports version `5.11`.
- The installed SDK assembly is compatible with that Directory.
- The Security Center license reports Visitor Management enabled.
- The bridge is started with the exact opt-in phrase `ENABLE SYNTHETIC VISITOR SYNC FOR NON-PRODUCTION`.
- A suitably restricted account may create/update native Visitor entities.
- The VMS booking remains in `Invited` status and has a valid local visit window that has not ended. The invitation upsert may run before or during the appointment; it writes the planned `VisitDate`/`Departure` and keeps native activation at `Never`. It does not record attendance. Only the separate guarded visitor-lifecycle endpoint may record a real check-in/check-out event after the VMS has recorded it. Expired appointments are rejected.

## Field mapping

| VMS field | Genetec target | Prototype mapping |
|---|---|---|
| `BookingId` | `Visitor.Name` | Stable key `STAMFORD VMS BOOKING {BookingId}`. Used for idempotent lookup/retry, not as the visitor's human-readable name. |
| Visitor first name | `Visitor.FirstName` (inherited cardholder field) | Set from parsed VMS name. |
| Visitor last name | `Visitor.LastName` (inherited cardholder field) | Set from parsed VMS name. |
| Start of `VisitWindow` / expected arrival | `Visitor.VisitDate` | The planned arrival date/time; SDK date-time setters convert local values to UTC. Keep it separate from native activation/check-in. |
| Activation before actual VMS check-in | `Visitor.Status.Activate(DateTime.MinValue, departureLocal)` | Set activation to `Never` and the scheduled expiry within the same SDK transaction so the invite remains available for manual check-in. Verify `ActivationType=Never`, no activation date, expected arrival and departure after independent read-back. The raw active `Visitor.CheckinDate` field is diagnostic only on the tested Directory because it mirrored Creation date while Security Desk still offered Check in. |
| End of `VisitWindow` | `Visitor.Departure` | Native expiration/departure time, supporting an overnight window up to 18 hours. |
| Actual VMS check-in event | `Visitor.Status.Activate(event time)` and `Visitor.Arrival=event time` | Only set at the recorded check-in event, then verify a fresh SDK session reports `Status.ActivationType=SpecificDate` and `Status.ActivationDate` matching the event. Keep raw `Visitor.CheckinDate` separate for diagnostics. |

| Host | None | Retained in VMS only; no approved mapping is assumed. |
| Destination / visit purpose | None | Retained in VMS only; no approved mapping is assumed. |
| QR token / email / phone | None | Not sent to Genetec by this operation. |
| Credential, cardholder group, escort, access rights | None | Not created or changed. |

## Idempotency and update behaviour

- Only booking IDs matching `VMS-[A-Z0-9-]{1,24}` are accepted.
- The bridge searches for the exact stable entity name before creating.
- Exactly one match is updated; zero matches create a native Visitor; multiple matches fail closed and require manual reconciliation.
- A retry after a client timeout should reuse the same booking ID. The stable entity key should find the same Visitor rather than create a second one.
- After a create/update, the bridge executes a follow-up server-side Visitor report/query for the returned GUID, then verifies the entity key, first/last name, planned `VisitDate`, `Departure`, `ActivationType=Never` and no activation date before reporting pre-registration success. The active entity's raw `Visitor.CheckinDate` is returned as `rawEntityCheckinDateUtc` for diagnostics, not used alone as proof of attendance.
- The API returns `operationId`, `visitorGuid`, whether the operation created or updated, the `idempotencyKey`, server-query/name/schedule read-back flags, activation type, and native check-in read-back. Bridge logs include the operation ID and synthetic booking reference.
- If the request times out or the read-back does not match, inspect the Genetec UI and the bridge logs before attempting a retry. Never infer write success from a frontend state change alone.

## Endpoint

`POST http://127.0.0.1:5190/api/genetec/visitor-booking`

Example body using synthetic data only:

```json
{
  "Confirmation": "SYNC_SYNTHETIC_STAMFORD_VMS_VISITOR",
  "BookingId": "VMS-SDKTEST-01",
  "FirstName": "Stamford VMS",
  "LastName": "Synthetic Test Visitor",
  "VisitDate": "2030-01-15",
  "VisitWindow": "09:00–10:00",
  "Status": "Invited"
}
```

Use an actual active appointment date/window when testing. The sample date above illustrates the payload only. A booking still in `Invited` state may sync even after its planned start has passed, while the appointment remains unexpired; verify that read-back remains `ActivationType=Never` and no activation date before allowing manual check-in.

## Known limitations and production decisions

- The invitation upsert endpoint accepts only VMS `Invited` status. A separate Day 2 lifecycle endpoint now supports a guarded candidate check-in/check-out mapping for synthetic bookings, but must not be treated as accepted until the non-production read-back test passes.
- Host, destination, purpose, email, phone, notification and QR fields are not mapped into Genetec.
- The bridge uses a Directory-backed exact-name `EntityConfigurationQuery` to find the stable entity name; `Engine.GetEntities()` alone is cache-only and must not be used as the idempotency lookup.
- Exact source-of-truth ownership, archival/checkout behaviour, field constraints, access windows/areas, permissions, retries, monitoring, retention, and production support remain part of the production integration design.
- The bridge has a separate loopback-only listener; it is a local prototype interface, not a production network service.


## Idempotency lookup correction (Day 2)

The original Day 1 implementation searched `engine.GetEntities(EntityType.Visitor)` before creating. This collection is the current Engine's local cache; a new Engine instance does not automatically contain every Visitor in the Directory. A retry could therefore create a second Visitor even when the first write/read-back succeeded.

The corrected implementation runs a server-side `EntityConfigurationQuery` filtered to `EntityType.Visitor` and exact entity `Name` (`StringSearchMode.Is`) using the stable `STAMFORD VMS BOOKING {BookingId}` key. Matching GUIDs are loaded from that query and the entity name is checked again. Zero matches permits create, one permits update, and more than one match is a hard stop with `DUPLICATE_VMS_VISITOR_ENTITIES`.

A read-only endpoint `GET /api/genetec/visitor-booking-lookup?bookingId=VMS-SDKTEST-01` is provided to diagnose and reconcile duplicate entities before retrying. The updated test script calls this endpoint first and refuses to write if more than one matching Visitor exists.


## Day 2 lifecycle event mapping (candidate; non-production verification required)

| VMS event | Genetec SDK operation | Acceptance read-back |
|---|---|---|
| `CheckIn` / status `Inside` | Call `Visitor.Status.Activate(event time)` and set `Visitor.Arrival` to the same actual event time. This is a candidate 5.11 mapping because the SDK documents arrival as activation date/time; it is not accepted on this Directory until activation state is independently read back. | Fresh Engine reports `Status.ActivationType=SpecificDate` and `Status.ActivationDate` matching submitted UTC event time within tolerance. The raw `Visitor.CheckinDate` value remains diagnostic. |
| `CheckOut` / status `Checked out` | Call `Visitor.Checkout()` after a verified check-in. | `VisitorManager.GetArchivedVisitor(originalGuid)` returns an archived Visitor in `VisitStates.CheckedOut` with native check-in/checkout timestamps; checkout time matches submitted event time within tolerance. |

The bridge checks the connected user's `CheckInVisitors` / `CheckOutVisitors` privilege, requires exactly one active Visitor matching the booking key, restricts booking IDs to `VMS-SDKTEST-*`, and keeps a durable local replay ledger. Exact retries return the saved prior operation. Any SDK mutation followed by failed read-back is an operational reconciliation case: inspect Security Center and bridge logs before attempting further writes.


## Day 2 correction: keep invitations pre-registered

The SDK XML supplied with the installed Security Center 5.11 SDK documents `Visitor.Arrival` as the activation date/time and `Visitor.CheckinDate` as the check-in date/time. Genetec's Visitor Management guide also states that a specific activation date automatically checks in the visitor at that date/time; the default `Never` activation is intended for visitors to be checked in manually later ([Genetec Security Center User Guide 5.12: Checking in new visitors](https://techdocs.genetec.com/r/en-US/Security-Center-User-Guide-5.12/Checking-in-new-visitors)).

The previous prototype incorrectly assigned the booked start time to `Visitor.Arrival`. If that time was already in the past, Genetec activated the profile and populated `CheckinDate` before the VMS recorded a check-in. The corrected mapping stores scheduled arrival in `Visitor.VisitDate`, writes activation as `Never` and its expiration in a single SDK transaction using `Visitor.Status.Activate(DateTime.MinValue, departureLocal)`, and stores scheduled departure in `Visitor.Departure`. It requires read-back proof of `ActivationType=Never`, no activation date, and matching arrival/departure. The raw active-entity `Visitor.CheckinDate` is reported separately because it mirrored creation time while Security Desk still offered Check in.

If an existing Visitor already has `CheckinDate` but no matching bridge lifecycle event, the bridge fails closed. It does not reset activation or rewrite the existing record; an authorised operator must reconcile the record. For retest, use a fresh synthetic booking ID such as `VMS-SDKTEST-06`; the test fixture schedules arrival ten minutes ahead. Do not write again to `VMS-SDKTEST-03`, `VMS-SDKTEST-04`, or `VMS-SDKTEST-05` without authorised inspection/reconciliation.

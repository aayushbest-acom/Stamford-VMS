# Day 2 incident: native Visitor retry created duplicate records

**Status:** Code fix prepared; must be built and verified against the installed Security Center 5.11 SDK before closure.

## Observed result

The Windows test sent the same booking (`VMS-SDKTEST-01`) twice. Both requests reported `created` and returned different Visitor GUIDs. This is a duplicate-creation defect, not a successful idempotency test. Stop further writes for this booking until the existing Genetec records have been inspected and reconciled by an authorised administrator.

## Root cause

The create/update decision used `engine.GetEntities(EntityType.Visitor)`. Genetec's Platform SDK documents this collection as cache-only: it returns Visitors currently known to that `Engine` and does not query the Directory to discover missing entities. The bridge creates a new `Engine` per HTTP request, so a retry can see an empty Visitor cache and create another entity even though the previous record exists server-side.

Reference: https://github-wiki-see.page/m/Genetec/DAP/wiki/platform-sdk-entity-cache

## Code correction

Before deciding to create a Visitor, the bridge now executes an `EntityConfigurationQuery` against the Directory with:

- `EntityTypeFilter = Visitor`
- `Name = STAMFORD VMS BOOKING {BookingId}`
- `NameSearchMode = Is`
- strict query results

The query's GUID results are loaded as `Visitor` entities and their `Name` is checked again. The outcome is fail-closed:

- **0 matches:** create may proceed.
- **1 match:** update may proceed.
- **More than 1 match:** return `DUPLICATE_VMS_VISITOR_ENTITIES` without a write.
- **Query error/unsupported query:** fail without a create/update decision.

Reference: https://github-wiki-see.page/m/Genetec/DAP/wiki/platform-sdk-report-manager

A new read-only endpoint, `GET /api/genetec/visitor-booking-lookup?bookingId=VMS-SDKTEST-01`, uses the same Directory-backed lookup and returns the matching GUIDs. `Test-NativeVisitorSync.ps1` now calls this endpoint before any write and refuses to continue if more than one match exists.

## Required recovery for the already-created test records

1. Build and start the patched bridge against the approved non-production Directory.
2. Run `Test-NativeVisitorLookup.ps1`. It should report the number of records found for the stable key and list the GUIDs.
3. If multiple matches remain, do not run `Test-NativeVisitorSync.ps1`. An authorised Genetec administrator must inspect the duplicate Visitors in Security Center and reconcile them according to local change-control procedures. Do not delete a record until confirming which is safe to retain.
4. Re-run the read-only lookup and confirm exactly one matching Visitor remains.
5. Then run `Test-NativeVisitorSync.ps1`. Pass criteria: create-or-update, exact retry and subsequent field update all return the same Visitor GUID with server-query, name and schedule read-back verification true.

## Separate lifecycle capability boundary

`GET /api/genetec/capabilities` is now implemented but explicitly reports visitor lifecycle sync as unsupported and disabled. `Test-NativeVisitorLifecycle.ps1` must continue to stop without writes until the native Security Center 5.11 check-in/check-out operation and its read-back mapping are implemented and verified.

## Validation status

Static source and project checks can be performed here, but the patched C# code has not been compiled or exercised against the user's licensed SDK host in this environment. The user-side Windows build and live query/test remain required evidence.

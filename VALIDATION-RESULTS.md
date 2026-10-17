# Validation record — visitor sync milestone

## Static checks completed in the build environment

- Frontend inline JavaScript passes `node --check`.
- `GenetecReadOnlyBridge.csproj` parses as valid XML.
- All bundled QR SVG files parse as XML.
- Fixed the reported CS0136 scope conflict by renaming the LINQ predicate parameter from `visitor` to `candidateVisitor` (no behavior change).
- `Program.cs` passed a source-level delimiter/brace balance check after the fix.
- Integration source contains the 5.11 version guard, Visitor Management license guard, explicit startup/request opt-ins, booking-key lookup, server-side Visitor report query, name/schedule read-back checks and operation correlation logging.
- Integration test script and mapping documentation are included.
- ZIP integrity was checked after packaging.

## Checks that still require the target Windows host

This environment does not have the user's licensed Genetec SDK install or Windows .NET Framework/MSBuild runtime. Therefore the C# bridge has **not** been compiled against the target SDK here and no live Genetec write has been executed from this environment.

On the approved Windows test host, build the bridge first, start it with `Start-Bridge.ps1`, verify `/api/genetec/status`, and only then enable native Visitor sync in the non-production environment. Run `Test-NativeVisitorSync.ps1` and inspect the created/updated synthetic Visitor in Security Center. Capture any build/runtime error output to complete the live validation.

## Safety boundary

The native Visitor endpoint only accepts `Invited` synthetic VMS booking IDs. It does not create credentials, assign escorts/groups, send invitations, or change access rights. Host, destination, purpose, email, phone and QR token remain VMS-only in this milestone. A failed operation/read-back must be checked in Genetec before retrying.


## Day 1 close-out refinements

- UI now updates each Day 1 gate from the live bridge response: authenticated connection, Directory version, and cardholder-group query.
- Fixed-cardholder-write status is labelled separately from native Visitor sync.
- Added `Test-BridgeReadOnly.ps1` for repeatable read-only gate verification with no write calls.
- PowerShell launcher and integration test have ASCII-safe strings for Windows PowerShell 5.1, avoiding no-BOM UTF-8 mojibake and protecting the test window separator.
- Startup cleanup removes credential-related environment variables and prompt variables after the bridge exits.
- Server Visitor report read-back scans all returned columns for the GUID instead of assuming it occupies column zero.

Live create/update/retry behaviour was subsequently verified by the user on the non-production Genetec 5.11 host; see the Day 2 operator evidence section below.

## Day 2 idempotency defect and corrective patch

- User-side reproduction: two identical `VMS-SDKTEST-01` booking sync calls both reported `created` and returned different Visitor GUIDs. This is recorded as a failed idempotency test, not a pass.
- Root cause in source: the upsert path used `Engine.GetEntities(EntityType.Visitor)` as if it queried the Directory. Genetec SDK docs describe this API as cache-only.
- Corrective source change: exact-name `EntityConfigurationQuery` with `EntityType.Visitor` is used before create/update; >1 match aborts with `DUPLICATE_VMS_VISITOR_ENTITIES`; query failure fails closed.
- Added read-only duplicate lookup route and `Test-NativeVisitorLookup.ps1`.
- Added pre-write duplicate detection to `Test-NativeVisitorSync.ps1`; it also quarantines the known `VMS-SDKTEST-01` key if lookup unexpectedly returns zero because prior logs proved two writes had succeeded.
- Added `/api/genetec/capabilities` with native lifecycle support/enabled/mapping flags all false. This makes the lifecycle test stop on an explicit unsupported capability instead of treating a missing route as the only guard.
- Local static validation: `git diff --check`, project XML parse, C# brace-balance and required-path checks passed; SDK XML shipped with the supplied Genetec SDK documents `EntityConfigurationQuery.Name`, `NameSearchMode`, `StrictResults`, `MaximumResultCount`, and `StringSearchMode.Is`.
- Still pending: compile this patch against the installed SDK on the Windows host; run `Test-NativeVisitorLookup.ps1`; reconcile duplicate Visitors; then run the create/retry/update test and verify all three operations return the same GUID. No live writes were performed during this patch preparation.


## Day 2 operator evidence — native Visitor upsert/idempotency

The user ran the patched bridge against Security Center Directory `5.11.3130.13` after reconciling/removing the earlier duplicate test record. For booking `VMS-SDKTEST-02`, the initial create, exact retry and subsequent field update all returned the same native Visitor GUID `1cf52dff-1c60-49f8-9bc2-a25990c003e2`. The test reported `NameReadbackVerified=True`, `ScheduleReadbackVerified=True`, and `ServerReportReadbackVerified=True`. Operation IDs were recorded by the bridge. This verifies the narrow native Visitor upsert/idempotency path on the target test host.

## Day 2 lifecycle path — implementation prepared, live test pending

The current worktree adds a guarded native Visitor check-in/check-out candidate, stable event IDs, persistent replay ledger, SDK privilege checks, active Directory key lookup, independent check-in or archived checkout read-back, UI lifecycle sync status, and `Test-NativeVisitorLifecycle.ps1`. The actual native lifecycle round-trip has not yet been reported by the user and is not accepted. The activation-based check-in mapping and `Visitor.Checkout()` mapping must pass on the target Security Center 5.11 host before capability status is treated as verified. The authoring environment has no licensed Genetec SDK runtime, so no C# build/live operation is claimed here.


## Pre-registration/activation correction (Day 2)

The first lifecycle preflight for `VMS-SDKTEST-03` returned `VISITOR_ALREADY_CHECKED_IN_UNTRACKED`. Inspection of the provided Security Center 5.11 SDK XML shows `Visitor.Arrival` is the activation date/time, while `Visitor.CheckinDate` is the native check-in date. The previous booking upsert wrote the planned start to `Arrival`, so a start time in the past could auto-activate the Visitor. The correction makes invitation sync set activation to `Never`, maps expected arrival to `VisitDate`, and rejects overwrites when a native check-in timestamp is already present without a corresponding bridge lifecycle event. This correction still requires build and live validation on the Windows Genetec host.


## Day 2 pre-registration activation/read-back investigation (latest)

- User confirmed the bridge builds successfully on Windows/Genetec 5.11 after the `System.IO.File` qualification patch. Three obsolete API warnings remain in the fixed test-cardholder path; they are non-fatal.
- For `VMS-SDKTEST-05`, Security Desk showed the **Check in** action available, `Activation` blank, and an invalid time-range warning. The SDK lookup showed `ActivationType=Never`, while the active entity's raw `Visitor.CheckinDate` matched the record creation time. This demonstrates that raw `Visitor.CheckinDate` alone is not a reliable active attendance signal in this observed case.
- For `VMS-SDKTEST-05`, the scheduled arrival was earlier than the record creation time, correlating with the invalid time-range warning. The next fixture now starts ten minutes in the future, and the bridge rejects invited upserts whose scheduled arrival is not more than one minute ahead.
- Current correction candidate uses `Status.ActivationType/ActivationDate` for canonical lifecycle state, exposes raw `Visitor.CheckinDate` separately for diagnostics, serializes local-time fields as local ISO values, and parses the lifecycle test appointment via `DateTimeOffset` before waiting.
- Current correction has not been compiled against the target SDK or live-tested by the user yet. Next safe test uses fresh key `VMS-SDKTEST-06`, then read-only lookup verifies exactly one Visitor, activation `Never`, no canonical activation check-in, future expected arrival and valid departure. Do not retry writes to `VMS-SDKTEST-03`, `VMS-SDKTEST-04` or `VMS-SDKTEST-05` without authorised inspection/reconciliation.


## Day 2 checkout failure and diagnostic hardening (current)

- Operator evidence: on `VMS-SDKTEST-06`, the native CheckIn operation and exact retry passed with the same Visitor GUID; the CheckOut request returned `CHECKOUT_FAILED`. This is **not** accepted as a lifecycle pass.
- The prior C# catch wrapped both `Visitor.Checkout()` and `GetArchivedVisitor()` under the same generic error code, so the HTTP error alone did not reveal which stage failed. The latest patch separates those stages and logs the SDK exception type/error code.
- After `Visitor.Checkout()` returns, the bridge polls archived state for up to 15 seconds, retrying only `SdkError.InvalidValue` (the installed SDK XML documents this code when the requested archived visitor is not found). Other SDK errors fail fast.
- Follow-up read-only probe for `VMS-SDKTEST-06` found no active Visitor and no archive while retaining the original Visitor GUID and check-in EventId. The investigation identified that the helper passed the original Visitor GUID to `GetArchivedVisitor(Guid)`, which expects an archived-visit GUID. The corrected helper uses `GetArchivedVisitorByCardholderId(originalVisitorGuid)` and exposes the archived-visit GUID separately. This patch has not yet been built/tested on the target Windows host.
- Added read-only `GET /api/genetec/visitor-lifecycle-state?bookingId=VMS-SDKTEST-*` and `Test-NativeVisitorLifecycleState.ps1` to inspect active-vs-archived state without writes. The lifecycle test invokes that diagnostic when a lifecycle POST fails and does not automatically repeat the write.
- Static checks pass as listed in the patch package; Windows compile and Genetec-host verification remain required. No live checkout pass is claimed.

## Day 2 operator evidence — native lifecycle round-trip

The user ran the native lifecycle harness on the approved non-production Genetec 5.11 Directory for `VMS-SDKTEST-07`. Create/upsert, exact retry and field update all retained Visitor GUID `33c34f69-44a3-46cf-b35f-e53d2d7937d3`; name/schedule/server-report read-back checks passed. Check-in, exact check-in retry, checkout and exact checkout retry all reported success/read-back verification. The final read-only query showed `activeMatchCount=0`, an archived `CheckedOut` visit, both lifecycle event IDs present, `savedCheckOutReconciledFromArchive=false`, and no credential/access-right changes.

### Follow-up correctness issue found during evidence review

The same output exposed the archive's raw `CheckinDate` as `2026-10-16T05:33:01Z`, while the activation-based check-in occurred around `2026-10-16T05:53:14Z`. Review of the checkout path found it was copying raw archived `CheckinDate` into the checkout event's canonical `GenetecCheckinDateUtc`. The observed native mutation and retry pass therefore does not, by itself, prove ledger timestamp provenance is correct. The new refinement preserves the prior independently verified canonical check-in timestamp, compares it with current native status before checkout, and adds read-only consistency flags. This follow-up has not yet been built or tested on the target host.

**Safe retest:** do not rerun lifecycle writes for `VMS-SDKTEST-07`; it is already checked out. Apply the follow-up patch, rebuild the bridge, inspect `VMS-SDKTEST-07` with `Test-NativeVisitorLifecycleState.ps1` (read-only; it may flag the existing checkout ledger for manual review), and then use a fresh unused synthetic booking to verify the corrected path.

## Milestone 4 local code validation — 16 October 2026

The source was updated after the operator screenshot showed native Visitor sync rejecting a visit window displayed as `09:00–10:00`.

- Browser, domain API and bridge now normalize common Unicode dash/minus/space variants before parsing the visit window.
- Secure kiosk check-in/out now creates a pending explicit Genetec lifecycle event from the local domain API's returned arrival/departure timestamp when a native Visitor GUID has already been verified.
- Lifecycle sync permits `VMS-SDKTEST-*` harness IDs and only the UI-generated `VMS-UI-` + six-hex ID shape. Each event ID must be prefixed by its exact booking ID. The existing lifecycle startup switch and per-request confirmation remain required.
- Added a one-click, non-interactive `Start-Bridge-VisitorLifecycleTest.cmd` for approved non-production synthetic lifecycle testing. Do not use it against production.
- Local verification: Python tests **26/26 passed**; Python compileall passed; inline JavaScript syntax passed; visit-window normalizer accepted 5 valid variants and rejected 3 invalid cases; the traceability matrix retains 110 unique requirement IDs.
- **Not verified here:** C# build against the installed Genetec SDK, PowerShell/DPAPI runtime, actual browser/device flow, and fresh native Visitor lifecycle read-back on the Windows host. The window-sync fix and lifecycle path remain unaccepted until those host checks pass.


## Build 5.1.5 — invitation upsert timing guard correction

- Removed the `arrivalLocal <= DateTime.Now.AddMinutes(1)` rejection from the guarded native Visitor invitation upsert. This guard made an un-checked-in appointment fail to synchronize once its start time approached or passed, even though the visit window remained open.
- The upsert continues to accept only synthetic bookings in `Invited` state, blocks bookings with recorded lifecycle events, validates the existing native Visitor activation state before an update, writes the planned VisitDate/Departure while setting activation to Never, and requires independent read-back.
- The bridge still rejects appointments whose scheduled departure has passed. Actual check-in/check-out must use the separate guarded lifecycle endpoint after the VMS records the event; invitation upsert cannot record attendance.
- Verification in this environment is limited to source-contract regression tests; a clean build and controlled live read-back against the installed Genetec 5.11 SDK remain necessary before acceptance.

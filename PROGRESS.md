# Stamford VMS prototype progress

Updated: 2026-10-18

## Latest milestone — 5.2.4 kiosk face-attendance workflow

- Added a kiosk option for Student, Staff and Contractor; webcam capture; mock/live indicator; candidate details and explicit confirmation; and a session-local attendance ledger.
- Mock mode uses scripted synthetic records only and never stores the webcam frame or writes to Genetec. Check Out requires a prior Check In in the same session; duplicate events are rejected.
- Live local matching remains gated and scoped to active, individually approved profiles linked to Genetec portraits and additional local camera samples. It is not a direct search over every Genetec Cardholder picture. The existing experimental LBPH matcher is not production-validated.
- Enriched selected-Cardholder metadata with first/last name, External ID and Cardholder Group names where the installed SDK exposes them; masked credential references only.
- Added `docs/CHANGE-REQUEST-KIOSK-FACE-ATTENDANCE.md`, `docs/KIOSK-FACE-ATTENDANCE.md` and `docs/VALIDATION-REPORT-MILESTONE5.2.4-KIOSK-FACE-ATTENDANCE.md`.
- Current local validation: **90/90 Python tests pass**, Python compile checks and inline JavaScript syntax check pass. C# bridge build/live read-only profile query require the supported Windows/Genetec SDK host.
- This adds a new biometric attendance purpose beyond the original SRS replacement-card workflow and needs formal safeguarding/privacy approval, category mapping, approved match accuracy/liveness thresholds, authentication/RBAC and a non-biometric fallback before real data is used.


## Current status

## Milestone 4 — visitor window and Genetec lifecycle wiring

- Added defensive visit-window normalization at the browser, domain API and bridge parsing layers; fixed the input class shown in the operator screenshot and added Unicode separator regression tests.
- Secure kiosk transitions now create a stable pending Genetec lifecycle event from the domain API's own arrival/departure timestamp for visits with a verified native Visitor GUID.
- Lifecycle bridge guard permits the UI's strictly shaped generated `VMS-UI-XXXXXX` synthetic IDs as well as the existing `VMS-SDKTEST-*` harness IDs; arbitrary `VMS-*` references remain blocked.
- Added `GenetecReadOnlyBridge/Start-Bridge-VisitorLifecycleTest.cmd` for explicit non-interactive test-mode launch in an approved non-production Directory.
- Local checks: **26/26 Python tests pass**, Python compileall passes, inline JavaScript syntax passes and 5 valid / 3 invalid visit-window normalization cases passed.
- Live Genetec C# build/SDK round-trip is still pending on the Windows host; do not mark M4 accepted until a fresh synthetic booking passes invitation sync, check-in/retry, checkout/retry and independent read-back.

Full steps: `docs/day4-MILESTONE4-VISITOR-GENETEC-WORKFLOW.md`.


**Historical supervised sales-demo completeness estimate: about 84%.** This is a prior engineering estimate for the interactive demo only; it is not the same as implementation coverage against the full requirements specification and is not production readiness. See `docs/REQUIREMENTS-TRACEABILITY.csv` for the per-ID assessment. Many mandatory/explicit production requirements remain partial, missing, or unverified.

### Changes in the latest implementation pass

- Replaced the generic static-file launcher with a loopback-only local service (`service/vms_service.py`) that serves the UI and saves the synthetic application snapshot in SQLite outside the repository.
- Added schema/size validation and optimistic revision checks; stale browser sessions are rejected with a visible conflict instead of silently overwriting a newer snapshot.
- Kept browser-local storage as an explicitly labelled fallback. This is still a whole-snapshot, unauthenticated prototype persistence layer—not a production domain API, authorization model or immutable audit store.
- Made predictable sample QR tokens explicitly labelled as demo-only and non-secure.
- Added 11 automated tests across the service and repository safety contracts. Python tests and browser-script syntax checks pass in this development environment.
- Added `docs/IMPLEMENTATION-STATUS.md` and a traceability CSV covering 110 SRS requirement IDs.

### Verified by operator output on the target Genetec 5.11 host

- The bridge built successfully against the installed SDK and connected to Directory `5.11.3130.13`; the read-only gate previously confirmed Visitor Management licensing.
- Native Visitor create/upsert, exact retry, and field update for `VMS-SDKTEST-07` retained GUID `33c34f69-44a3-46cf-b35f-e53d2d7937d3`. Name, schedule, and server-report read-back checks passed.
- `VMS-SDKTEST-07` completed native check-in, identical check-in retry, native checkout, and identical checkout retry. Both lifecycle responses reported read-back verification, and both retries returned the same Visitor GUID.
- A subsequent read-only query confirmed no active Visitor, one archived visit in `CheckedOut` state, both check-in and checkout event IDs, and `savedCheckOutReconciledFromArchive=false`. Credentials and access rights were unchanged.
- `VMS-SDKTEST-06`'s already-completed checkout was safely reconciled into the local event ledger from read-only archived evidence. That reconciliation issued no Genetec write.

### Newly identified correctness refinement

The `VMS-SDKTEST-07` archive result shows raw archived `CheckinDate` at `2026-10-16T05:33:01Z`, while the actual activation-based check-in event occurred around `05:53:14Z`. Source review found that the normal checkout handler was copying the raw archive `CheckinDate` into the checkout ledger's canonical `GenetecCheckinDateUtc`. The native checkout and retry passed, but this timestamp provenance mismatch must be corrected before treating the event ledger as fully accepted.

The pending patch preserves the independently verified `Visitor.Status.ActivationDate` timestamp, checks it against the prior verified check-in ledger before invoking checkout, and exposes explicit read-only consistency fields. Do not repeat lifecycle writes for `VMS-SDKTEST-07`; it is already checked out. Apply the correction, build on the Windows SDK host, inspect `VMS-SDKTEST-07` read-only, and use a fresh synthetic booking (for example `VMS-SDKTEST-08`) to verify the corrected normal path.

### Implemented, still needing browser/host acceptance

- Genetec-native Visitor upsert and guarded check-in/check-out bridge paths with stable Event IDs, explicit write opt-ins, and independent native read-back.
- Local-ledger reconciliation for a checkout that Genetec completed but the bridge failed to record.
- Browser-local persistence of synthetic VMS records, linked GUIDs, and lifecycle events across refresh; explicit Genetec status and duplicate-click guarding.

### Remaining for a credible complete demo

1. Apply/build/test the canonical check-in timestamp refinement with a fresh synthetic booking; confirm read-only archive/ledger consistency is true.
2. Browser-accept the UI-to-bridge-to-Genetec journey: create invitation, sync, check in/out, refresh/reconnect, observe truthful sync status and audit events, and inspect bridge-offline/error behavior.
3. Exercise exception paths: cancelled/expired invitation, duplicate QR, bridge offline, request timeout after commit, conflicting Event ID payload, and bridge restart/replay.
4. Polish seed/reset states, a concise demo script, and a visible limitations view.
5. Production requirements remain: authentication/RBAC, server-side persistence/outbox, privacy/retention, hardening, backup/restore, load testing, full Genetec UAT, and customer sign-off.

## Acceptance terminology

- **Implemented:** code exists.
- **Built:** the target Windows SDK project compiles.
- **Verified:** the test on the specified Genetec Directory passed and returned independent read-back evidence.

Do not promote a feature from implemented to fully verified just because the bridge returns HTTP success or the UI updates. The native mutations and idempotent retries passed for `VMS-SDKTEST-07`, while canonical checkout-ledger timestamp provenance remains pending correction and re-test.


## Build 5.1.3 — time entry and QR visit-window correction
- Time controls allow minute-level values (`step=60`), not 15-minute increments.
- Visit checks now interpret date/time in the school timezone (Asia/Singapore, UTC+08:00) instead of the Windows host timezone.
- QR validation returns the school-local current time, scheduled start/end, and allowed check-in interval to make rejected scans diagnosable.


## Build 5.1.4 — today's default date and invitation amendments
- New invitations default to today's Asia/Singapore school-local date while keeping future dates selectable. This corrects the previous default-to-tomorrow UX that caused scans on 17 October to be rejected for visits scheduled on 18 October.
- Added a schedule-amendment endpoint for active invited server-backed visits. It validates date/time, updates the QR check-in window and expiry, preserves the current non-expired token, increments row version, and records before/after values in the domain audit log.
- Added an Edit visit date/time action in visit details. If the invitation already has a Genetec Visitor GUID, the UI instructs the operator to explicitly re-sync the updated schedule after saving.
- QR rejection messaging calls out the booked school-local date/window, current school time, and allowed check-in bounds without repeating the same timestamp line.
- Regression suite: 40/40 passing; live Windows/Genetec UAT remains to be rerun on the target workstation.


## Build 5.1.5 — allow syncing invited bookings during their visit window
- Removed the overly strict Genetec bridge guard that required scheduled arrival to be more than one minute in the future. This guard rejected still-Invited bookings even when the visitor had not checked in and the appointment window was still open.
- Invitation sync is still restricted to VMS `Invited` synthetic bookings and still checks for existing lifecycle events and native activation state. The upsert writes planned `VisitDate`/`Departure` with activation `Never`; it does not record check-in.
- Expired appointment windows remain blocked. Real check-in/check-out must still use the separate guarded lifecycle path after VMS records the event.
- Updated operator-facing mapping docs and bumped UI/service/launcher version to 5.1.5. The bridge now reports its build identifier; auto-start detects a stale/unversioned local bridge and restarts it only when port 5190 is owned by this prototype executable, avoiding silently reusing the old safety guard.
- Validation in this environment: Python regression tests and static JavaScript/C# source-contract checks; live Windows SDK/Genetec build and read-back remain required on the target host.


## Build 5.1.6 — checkout recovery ordering (17 October 2026)
- Diagnosed the observed rejection: the local VMS had a pending CheckIn event and a failed CheckOut event, but the bridge correctly had no successfully read-back-verified check-in ledger entry.
- Updated visit details to offer the original CheckIn event as the next step if the VMS already records checkout locally and Genetec check-in remains unsynced. Once check-in is independently verified, the modal offers the same saved CheckOut event for retry.
- The sync function blocks checkout until check-in is `Synced`; historical check-in recovery is allowed only when both original events exist in chronological order and the original check-in is within the bridge's 30-minute new-event window. Otherwise it directs the operator to inspect/reconcile rather than inventing timestamps.
- Removed stale post-render button disabling that could have disabled the checkout button using the just-completed check-in event's state.
- Versioned UI, service, launcher and bridge build as 5.1.6 so auto-start can replace the previous bridge build.
- Validation: 42/42 Python regression tests; Python compileall; Node.js inline JavaScript syntax check. Live Windows/Genetec lifecycle round-trip must still be run on the target SDK host.

## Latest change — Build 5.1.6 lifecycle retry ordering

The frontend now detects when a local visit is already `Checked out` but its Genetec CheckIn event remains Pending/Failed. It presents the original CheckIn as the next action, blocks checkout submission until CheckIn has a read-back-verified `Synced` state, and then exposes the original checkout event. Recovery is limited to both existing, chronologically ordered events where the check-in event is within the bridge's 30-minute event window. Uncertain/older events require read-only inspection and operator reconciliation. This sequence has local regression coverage but still requires the target Windows/Genetec SDK round-trip. See `MILESTONE5.1.6-CHECKOUT-SEQUENCING.md`.

## Milestone 5.1.3 — QR timing and minute-level visit times

- Visitor start/end fields use `step=60`, permitting any minute instead of 15-minute increments.
- Visit date defaults/minimum are computed in school-local Asia/Singapore calendar date, regardless of the laptop timezone.
- Server-side visit-window and QR-pass expiry calculations use Asia/Singapore (UTC+08:00), not the host OS timezone; QR validation returns the current school-local time and allowed start/end diagnostics.
- Failed QR transition messages now include the server's school-local clock and permitted check-in interval so timing errors can be diagnosed directly.
- Automated regression suite: 38 tests pass; Python compilation and JavaScript syntax checks pass.
- Pending on the target Windows host: verify an invitation with an arbitrary minute (e.g. 10:07–10:38) and perform a QR scan at the corresponding Singapore-local time.

# Stamford VMS implementation status

**Snapshot:** 16 October 2026  
**Disposition:** Development prototype only; not production-accepted.


## Milestone 5 — automatic Genetec connection and front-end usability

- `Start-Demo.cmd` now invokes `GenetecReadOnlyBridge/Start-Bridge-Automatically.ps1`. If the bridge is not already listening and local DPAPI profile/SDK prerequisites exist, it builds the bridge non-interactively when its executable is missing and starts the bridge hidden with the guarded synthetic visitor upsert and lifecycle scopes, redirects logs to `%LOCALAPPDATA%\StamfordVMS\Logs`, and waits briefly for a live status response. It does not spawn a duplicate bridge when one is already running. If prerequisites are missing, the VMS still opens.
- The overview probes Genetec status/capabilities automatically when the application loads, updates a dashboard connection banner, and suppresses toast/audit noise for the background probe. The integration diagnostics page remains optional.
- Removed the user-facing `Day 1` / `Day 2` labels and renamed the diagnostics navigation/title to `Genetec connection`.
- Replaced the invitation's fixed visit-window dropdown with required, blank Start time and End time pickers. The UI rejects missing times and an end time that is not later than the start time; it sends a normalized `HH:MM–HH:MM` range to the existing domain API.
- Added laptop-camera QR scanning to the kiosk. The browser requests camera permission and uses native `BarcodeDetector` QR detection where available. A detected token is sent into the existing server QR validation and transactional check-in/check-out path. Manual entry remains an explicit fallback, and the camera stream is released when the kiosk is reset or left.
- Automated regression tests: **29/29 PASS**. Node.js inline JavaScript syntax check: **PASS**. Python bytecode compilation: **PASS**.
- **Still pending on the target Windows host:** PowerShell helper execution, DPAPI/profile load in the expected Windows account, browser camera permission and QR decode test, fresh bridge startup connection, and native Genetec end-to-end checks. These are not claimed as completed by static contracts.

Safety boundary remains: prototype-only, synthetic data only, loopback-only, and no enterprise authentication/RBAC. The browser still uses the local bridge as the SDK adapter; auto-start and auto-probe remove the need for a separate manual bridge startup/page visit, but do not make the browser load the Genetec SDK directly. Writes still happen only after the applicable UI operation and confirmation, within bridge-side test guards.

## Milestone 4 — visitor-window robustness and UI lifecycle wiring

- Fixed the reported `INVALID_VISIT_WINDOW` class of failure by normalizing Unicode dash/minus, full-width punctuation, non-breaking spaces and invisible formatting characters in the browser, local domain API and Genetec bridge.
- Canonicalized saved domain visit windows and added regression tests for the exact class of input that can look like `09:00–10:00` while containing a different separator code point.
- Wired secure kiosk check-in/out events to create a separate Genetec lifecycle event using server-returned timestamps when the booking already has a verified native Visitor GUID.
- Extended the bridge allowlist only to the generated UI booking form `VMS-UI-` + six hexadecimal characters alongside the existing `VMS-SDKTEST-*` test harness IDs. Lifecycle event IDs must be bound to the exact booking ID. Lifecycle writes remain separately gated and request-confirmed.
- Added `Start-Bridge-VisitorLifecycleTest.cmd` for non-interactive **non-production synthetic lifecycle testing**; `Start-Bridge-VisitorSync.cmd` continues to enable invitation upsert only.
- Automated tests: **26/26 PASS**; Python bytecode compile, browser inline JavaScript syntax and five window-normalization variants pass locally. See `docs/day4-MILESTONE4-VISITOR-GENETEC-WORKFLOW.md`.
- **Still pending:** build this exact C# source on the Genetec Windows host and perform a fresh native Visitor sync/lifecycle round trip. The browser now normalizes the request, but the previous screenshot's failure is only proven fixed after the target-host retry and read-back pass.

## Milestone 2 — work completed in this bundle

1. Added `service/domain.py` with a normalized SQLite visitor table, transactionally enforced check-in/check-out/cancellation/pass rotation, server UTC timestamps, stable event IDs, idempotent exact retries, and server-side prototype audit rows.
2. Added opaque QR passes issued using `secrets.token_urlsafe(32)`. SQLite stores only a SHA-256 digest; the raw token is returned at creation/rotation and is omitted from the whole-snapshot state sent to SQLite and browser `localStorage`.
3. Added dynamic server-rendered SVG QR images, token validation, expiry/revocation checks, booking-window checks, and a keyboard-wedge scanner input path for target-hardware validation.
4. Connected secure invitation creation, visit details/pass rotation, cancellation, and kiosk check-in/out to the domain API. Seeded legacy records remain clearly separated as simulation-only records. The domain API does not silently mutate Genetec.
5. Added `GenetecReadOnlyBridge/Genetec-CredentialStore.ps1` and `Save-GenetecCredentials.ps1`. Connection fields are stored outside the repository in a Windows DPAPI `CurrentUser`-encrypted profile with restrictive local ACLs.
6. Reworked `Start-Bridge.ps1` to be non-interactive: no `Read-Host` prompts during startup, fail-fast messages if the one-time profile/SDK/executable is missing, read-only defaults, and explicit switches for guarded test write scopes. Added the double-click `Start-Bridge.cmd` wrapper and a clearly named `Start-Bridge-VisitorSync.cmd` launcher for the explicit visitor-sync scope.
7. Renamed the launcher variable so it does not shadow PowerShell's automatic `$PROFILE` variable. Added source-contract tests for DPAPI safety, non-interactive startup and excluding raw secure QR tokens from browser snapshots.
8. Updated the requirements traceability file and handover notes. All 110 SRS IDs remain in the matrix.

## Milestone 3 — non-interactive Genetec startup

- Removed runtime `Read-Host` calls from `Start-Bridge.ps1`. A valid saved profile, SDK and executable are required; missing prerequisites produce a direct error instead of an input prompt.
- Added `Start-Bridge.cmd` as the no-prompt read-only launcher, `Start-Bridge-VisitorSync.cmd` as a one-click explicit visitor-sync launcher, and command-line switches for optional guarded test scopes. A normal launch is read-only.
- Request-level confirmations and synthetic-record restrictions remain; the independent write test scripts remain interactive because they can mutate Genetec or reconcile state.

## Validation results in this environment

- Python automated tests: **26/26 PASS** (`python -m unittest discover -s tests -v`).
- Python syntax/bytecode compilation: **PASS** for the service modules and test modules.
- Inline UI JavaScript syntax: **PASS** using Node.js `--check`.
- Genetec project file XML: **PASS**; all **106/106** bundled QR SVG files parse as XML.
- PowerShell parser/runtime test: **NOT RUN**. PowerShell is unavailable in this environment; the DPAPI/ACL script must be exercised on the target Windows user profile.
- .NET bridge build and live Genetec SDK test: **NOT RUN**. The .NET SDK and Stamford's installed/licensed Genetec SDK/Directory are unavailable here.
- Interactive browser UAT: **NOT COMPLETED** in this environment. Static JavaScript checks and HTTP-level tests are not a substitute for target browser, QR scanner, printer and accessibility testing.

See `VALIDATION-REPORT.md` for the checks and limitations.

## Architecture and safety boundary

The original `/api/v1/state` endpoint is still a whole-snapshot prototype persistence service: the browser authors arrays such as seeded groups, card requests, activities and illustrative audit entries. Secure visitor operations now use a **separate transactional domain API** and separate SQLite tables with state-transition checks and token hashing. This does not make the app production-secure: there is no application authentication or role-based authorization, the prototype audit table is not tamper-resistant, and UI snapshot content is not a trusted audit source. The health endpoint explicitly reports `authentication=not-configured`. Keep services loopback-only and use synthetic data only.

Genetec credentials are not included in the source/archive. The one-time local profile is tied to the same Windows user and machine by DPAPI. No credential profile was created by this development environment, and the PowerShell/DPAPI behavior remains unverified on the target host. The normal startup path no longer asks questions. Mutating scopes remain disabled by default and are enabled only via explicit launcher switches; request-level guards in the bridge remain active. No PowerShell runtime test was possible in this environment.

## P0 — production blockers

- On the target Windows machine, run the credential-store setup once using a dedicated least-privilege SDK account. Since a reusable credential value was shared in chat, rotate it before use. Then test the DPAPI profile save/load as the same Windows user.
- Build the bridge against the installed/licensed Genetec Security Center 5.11 SDK. Re-run the native Visitor create/update plus fresh synthetic check-in/check-out read-back tests after the latest timestamp-provenance correction; record results from the Directory itself.
- Add approved application authentication/RBAC, actor-aware authorization on each domain endpoint, rate limits, server-side input policy, approved CSRF/threat controls for the final deployment, and durable/tamper-evident audit.
- Approve the source-of-truth/field ownership matrix, supported/licensed SDK/API mapping, cancellation/state synchronization, physical access window/area enforcement, retry/outbox/reconciliation strategy and controller-authoritative negative tests.
- Obtain Stamford privacy/safeguarding approval before real visitor identity data or biometric features are enabled. Keep group photos separate from biometric enrollment.

## P1 — required core workflows

- Host invite amendment, approved SMS/notification integration, real printer driver/bridge and visible recoverable print exceptions.
- Overdue timer/host notification/security escalation, host-approved extension, missed-checkout UI and reasoned audited reconciliation.
- Replacement-card eligibility, approved face/liveness provider (only if authorised), safe exception routing, lost-credential revocation before replacement activation, and recorded physical collection.
- Group arrival/departure camera capture, associated timestamps, roster-level reconciliation, missing/obscured attendee handling, and validated large-group procedure.
- Camera stream integration, local inference for approved detectors, event/camera/time context and operator routing. Measure false alerts and missed scripted events separately for each included detector.

## P1 — operations and acceptance

- Retention/deletion, transport/storage encryption, approved egress, hardening/patching, monitoring, remote-support controls, backup/restore and signed RPO/RTO.
- Device/network/site compatibility and accessibility survey, kiosk/iPad/printer/scanner UAT, manual fallbacks, support SLA, role-specific training and rollback runbook.
- Approve numerical latency/availability and concurrency targets. Capacity planning must cover the SRS estimates of ~60 average visits/day and up to ~500 on a busy day plus an agreed arrival-burst model.
- Complete formal UAT and sign off every mandatory/explicit requirement included in the pilot with evidence or an accepted exception.

## Genetec credential setup

See `GENETEC-LOCAL-CREDENTIAL-SETUP.md`. Save the connection profile once on the target Windows workstation, then the bridge launcher automatically loads it. Do not store the credential values in the repository or share the DPAPI file; use the same Windows user and machine. Normal launch is read-only and non-interactive. Use explicit launcher switches only for approved non-production testing with synthetic records.


## Build 5.1.3 — time entry and QR visit-window correction
- Time controls allow minute-level values (`step=60`), not 15-minute increments.
- Visit checks now interpret date/time in the school timezone (Asia/Singapore, UTC+08:00) instead of the Windows host timezone.
- QR validation returns the school-local current time, scheduled start/end, and allowed check-in interval to make rejected scans diagnosable.


## Milestone 5.1.4 — visit date / QR error clarification

The invitation form now defaults to today's date in Asia/Singapore rather than tomorrow. The date remains selectable for future invitations. Active invited server-backed bookings can be amended through `POST /api/v1/visits/{bookingId}/amend`; a valid live QR token is preserved, expiry is recalculated, row version is incremented, and an audit entry records before/after schedule values. QR-window errors explicitly report the scheduled date/window, current school-local time and allowed boundaries. If already synchronized to Genetec, the operator must re-sync the amended schedule and verify read-back. Automated tests pass 40/40; live browser/Genetec runtime remains to be confirmed.


## Build 5.1.5 correction
Genetec invitation sync now allows still-Invited, not-yet-checked-in synthetic bookings to sync during an open appointment window even if the scheduled arrival time has already passed. The bridge writes planned schedule fields with ActivationType=Never and still rejects expired windows, pre-existing lifecycle events, ambiguous native activation state, and unapproved booking IDs. Live target-host verification is required.

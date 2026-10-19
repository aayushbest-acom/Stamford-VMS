# Stamford VMS Prototype Repository

**Project:** ACOM × Stamford American International School — Visitor Management System
**Current milestone:** 5.5.0 — local accounts, session authentication and server-enforced feature-level permissions, building on reporting, Group Visits, visitor types and Stamford branding
**Repository status:** Prototype source, synthetic data, and a loopback-only .NET Framework bridge. Not a production release.

## What is in this repository

- `ui/` — Stamford-branded browser prototype. Visitor, kiosk, group, replacement-card and AI-review workflows remain demonstrative unless explicitly stated otherwise.
- `service/vms_service.py` — loopback-only HTTP service with SQLite snapshot persistence plus dedicated visitor-domain routes. `service/domain.py` implements transactional visit transitions and secure QR validation; the service enables local prototype accounts and server-enforced feature permissions, but is not a production identity platform.
- `tests/` — automated regression tests for snapshot persistence, domain transitions, reporting/export integrity, QR token handling, retry idempotency, and source/credential-store safety contracts.
- `docs/REQUIREMENTS-TRACEABILITY.csv`, `docs/IMPLEMENTATION-STATUS.md` and `docs/VALIDATION-REPORT.md` — requirement-by-requirement delivery status, validation evidence and remaining acceptance gates. See `docs/GENETEC-LOCAL-CREDENTIAL-SETUP.md` for saving bridge credentials once.
- `GenetecReadOnlyBridge/` — .NET Framework 4.8 bridge, using the locally installed Genetec Security Center 5.11 SDK. It binds only to `127.0.0.1:5190`.
- `docs/Genetec-Visitor-Field-Mapping.md` — current narrow mapping for invited synthetic bookings to a native Genetec Visitor.
- `DAY1-CLOSEOUT.md`, `VALIDATION-RESULTS.md` — implementation boundary and validation history.
- `docs/REPORTING-EXPORTS.md` — report definitions, filters, supported formats, API endpoints and source limitations.
- `patches/` — patch queues intended to be applied incrementally. See `patches/README.md`.

## Run the frontend

On Windows with Python 3.10+ installed, from the repository root:

```powershell
.\Start-Demo.cmd
```

Open <http://127.0.0.1:8080>. Keep the launcher window open while using the prototype. The launcher serves the UI and local SQLite-backed API, checks for the optional local OpenCV QR decoder and installs it through pip when missing (internet is needed only for that first install), and attempts to start the Genetec bridge in the background using the saved Windows DPAPI profile. If a bridge is already running, it is reused; if the bridge executable is missing, the helper attempts a non-interactive build against the SDK path from the saved profile/default installation. If connection prerequisites are missing, the VMS still starts and the overview shows the connection status. The bridge logs are written under `%LOCALAPPDATA%\StamfordVMS\Logs`. The local database is stored outside the repository under the current user's local application-data directory by default. Set `STAMFORD_VMS_DATA_DIR` to override it. The UI falls back to browser-local storage if the service is unavailable and clearly marks that state.

Run the automated local checks from the repository root:

```powershell
python -m unittest discover -s tests -v
```

These checks exercise the SQLite prototype API and static safety contracts; they are not a substitute for browser UAT or the Windows/Genetec SDK integration tests.

## Build and run the Genetec bridge

Use a Windows workstation with the .NET Framework 4.8 targeting/developer pack, a compatible licensed Genetec Security Center 5.11 SDK, and an approved least-privilege account.

```powershell
$env:GSC_SDK = 'C:\Program Files (x86)\Genetec Security Center 5.11 SDK'
cd .\GenetecReadOnlyBridge
dotnet build .\GenetecReadOnlyBridge.csproj /p:Configuration=Debug
.\Start-Bridge.ps1
```

Provision the encrypted Genetec profile once with `./GenetecReadOnlyBridge/Save-GenetecCredentials.ps1` (see `docs/GENETEC-LOCAL-CREDENTIAL-SETUP.md`). Normal starts are now non-interactive: use `GenetecReadOnlyBridge/Start-Bridge.cmd` or `Start-Bridge.ps1`; the saved profile is loaded automatically for the same Windows user/machine. The bridge defaults to read-only mode and no longer asks three write-scope questions at startup. For an approved non-production test environment only, use `GenetecReadOnlyBridge/Start-Bridge-VisitorSync.cmd` for a one-click visitor-sync start, or pass `-EnableVisitorSync` to `Start-Bridge.cmd`; lifecycle and test-cardholder writes have their own switches and remain disabled otherwise. For one-click supervised lifecycle tests, `Start-Bridge-VisitorLifecycleTest.cmd` enables the two guarded visitor scopes without startup prompts; use it only with synthetic visits on an approved non-production Directory. The bridge's request-level confirmation, synthetic-record restrictions, version/licence checks and privilege checks remain in force. If the credential profile, SDK or built executable is missing, startup fails immediately with an actionable error instead of prompting. Keep the listener loopback-only; never test against real student, staff, visitor or access credentials.

## Day 1 evidence and limitations

According to the operator-provided test output on 15 October 2026, the read-only gates passed against Directory `5.11.3130.13`: connection/authentication, version, cardholder-group query (11 groups), Visitor Management licence and visitor-sync startup flag. The UI was also started on `127.0.0.1:8080`.

The user subsequently ran the native Visitor upsert test in the non-production Directory and verified initial create, exact retry and update all returned the same GUID, with name, schedule and server-report read-back passing. See `VALIDATION-RESULTS.md`.

The bridge has not been compiled by the repository author against the site's installed SDK in this environment. SDK-host build and live test evidence must be retained before moving any integration change into a production path.

## Native Visitor integration notes

Day 2 includes a Directory-backed idempotency correction for native Visitor booking upserts and a read-only duplicate-lookup endpoint. A read-only lifecycle state endpoint now inspects active versus archived Visitor status. Archive read-back uses `GetArchivedVisitorByCardholderId(originalVisitorGuid)`, because the persisted original Visitor GUID is not the archived-visit GUID required by `GetArchivedVisitor(Guid)`. Checkout read-back polls for eventual archive visibility with staged SDK diagnostics. Visitor arrival/departure lifecycle synchronisation is now implemented as a separately guarded candidate path, but is **not yet accepted**. Invited booking sync keeps `Visitor.Status.Activate(DateTime.MinValue)` (activation Never) and stores the planned arrival in `Visitor.VisitDate`; only the actual check-in event sets specific activation/`Visitor.Arrival`. `Visitor.Checkout()` is used for checkout. It requires live native timestamps / archived-state read-back on the target 5.11 host and exact-event retry checks before it can be treated as verified. Updating scheduled `Arrival`/`Departure` fields alone is not treated as check-in/check-out.

See `patches/README.md` and the Day 2 patch queue for the staged contract and test harness.

## Security

- Never commit credentials, certificates, tokens, site-specific connection settings or diagnostic dumps containing secrets.
- Do not copy configuration files from the upstream sync utility or read-only POC into this repository.
- A certificate-like value was included in chat output during development. If it is active, rotate/revoke it before further use.
- Do not expose the bridge to the LAN/Internet or remove the explicit non-production write guards for convenience.
- Review `SECURITY.md` before making or applying changes.


## Front-end integration and usability

The browser prototype now saves its synthetic application snapshot to local SQLite through a loopback-only service and keeps localStorage as a fallback. Revision checks reject stale writes rather than silently overwriting newer state. The browser automatically probes Genetec status/capabilities on startup and updates the dashboard; opening the integration diagnostics page is optional. The create-invitation form requires explicit start and end times. The kiosk requests the laptop camera and uses native `BarcodeDetector` QR decoding where supported; otherwise, downscaled frames are posted only to the loopback VMS service and decoded locally by OpenCV. Camera frames are not written to disk or logged. Manual token entry remains as a fallback. This is still a prototype snapshot store—not an authenticated multi-user transactional VMS API, tamper-resistant audit service, or approved production data store. See `docs/day2/UI-LIFECYCLE-PERSISTENCE.md`, `docs/IMPLEMENTATION-STATUS.md`, and `PROGRESS.md`.

## Native Visitor lifecycle

The separately gated native Visitor lifecycle endpoint is restricted to synthetic bookings (`VMS-SDKTEST-*` harness IDs or strictly shaped prototype-generated `VMS-UI-XXXXXX` IDs). On the target Security Center 5.11.3130.13 host, the operator has verified create/upsert, exact retry, field update, check-in/retry, checkout/retry, and archived `CheckedOut` read-back for `VMS-SDKTEST-07`. Evidence review then identified a timestamp provenance refinement: raw archived `Visitor.CheckinDate` can reflect entity creation time, so the checkout ledger must retain the independently verified activation-based check-in timestamp from the prior CheckIn event. The correction is prepared but still requires Windows SDK build and a fresh end-to-end retest before the full lifecycle-ledger contract is accepted. See `docs/day2/LIFECYCLE-IMPLEMENTATION-STATUS.md`, `docs/day2/CANONICAL-CHECKIN-TIMESTAMP-PROVENANCE.md`, and `docs/day2/VISITOR-LIFECYCLE-CONTRACT.md`.

## Recovery after an archive-confirmed checkout

If a Genetec checkout succeeded but the original API call failed before the local ledger was updated, inspect state first with `Test-NativeVisitorLifecycleState.ps1 -BookingId VMS-SDKTEST-06`. If the response proves there is no active Visitor and exactly one mapped archived visit in `CheckedOut` state, with a verified check-in event but no checkout event in the ledger, run `GenetecReadOnlyBridge\Reconcile-NativeVisitorCheckout.ps1 -BookingId VMS-SDKTEST-06`. This is a guarded local-ledger reconciliation, not a Genetec write. Never repeat checkout just to repair the local ledger.


## Day 3: transactional visitor API and secure QR passes

The local Python service now includes a separate SQLite-backed domain API for synthetic visitor bookings:

- `POST /api/v1/visits` creates an invited visit and returns a cryptographically random, opaque QR token once. SQLite stores the token's SHA-256 digest only.
- `GET /api/v1/visits` and `GET /api/v1/visits/{bookingId}` return booking metadata without a raw QR token.
- `POST /api/v1/visits/{bookingId}/qr/rotate` replaces an invited visit's pass and makes the previous token unrecognisable.
- `POST /api/v1/visits/{bookingId}/cancel` cancels an invited visit and revokes its token.
- `POST /api/v1/visits/{bookingId}/amend` updates the date/time of an active invited visit, preserves a live QR token, recalculates expiry, and records a schedule amendment in the local audit table; linked Genetec schedules must be explicitly re-synced.
- `POST /api/v1/qr/validate`, `/api/v1/qr/check-in` and `/api/v1/qr/check-out` validate passes and execute visit-state transitions transactionally. Check-in enforces the configured booking time window; state transitions accept stable event IDs for idempotent retries.
- `POST /api/v1/qr/svg` produces a QR image for a currently valid token. The raw token is sent in the request body, not a URL or server log.

New secure visits created in the UI use the domain API. The browser only keeps the raw pass in memory for QR rendering; it is omitted from localStorage and the whole-snapshot persistence payload. After reload, the UI can issue a replacement pass for an invited booking; rotation revokes the prior pass. Seeded records remain explicitly marked demo simulations.

The UI uses local account authentication and server-enforced role/permission checks. This API is still loopback-only and synthetic-data-only; it is a prototype access-control implementation, not production-approved. Before real identities or public use, add approved authentication/authorization, rate limits, CSRF/threat controls for the final deployment, a transactional production data model, signed-off policies and the remaining acceptance evidence.

## Day 3: saved Genetec connection profile

`GenetecReadOnlyBridge/Start-Bridge.ps1` now has no startup prompts. The server, user, certificate value, password, port and SDK directory are loaded from the DPAPI CurrentUser-encrypted file under `%LOCALAPPDATA%\StamfordVMS\Secrets`, never from source or repository config. Write scopes default to disabled; select a scope explicitly with a launcher switch only when using approved non-production synthetic records. The profile only decrypts for the same Windows user on the same machine. If the profile was never provisioned, run `Save-GenetecCredentials.ps1` once; the normal launcher will not prompt or accept credentials on the command line.


## Front-end usability updates

`Start-Demo.cmd` runs `GenetecReadOnlyBridge/Start-Bridge-Automatically.ps1` before starting the UI. When the saved DPAPI profile and SDK are available, the helper builds the bridge on the Windows host if its executable is missing, starts the local loopback bridge hidden with guarded synthetic visitor sync and lifecycle scopes, then waits briefly for connection verification. It does not expose the bridge on the LAN. If the profile or executable is missing, it reports a warning and still starts the VMS. A bridge already listening is reused rather than duplicated.

The overview automatically probes the Genetec connection; the connection diagnostics screen is optional. Startup does not itself create visitor records or perform a visitor lifecycle write: the individual UI operations remain explicit and subject to request confirmation. The camera scanner requires current Microsoft Edge or Chrome with camera permission and native `BarcodeDetector` QR support. On unsupported browsers, manual token entry remains available.

See `docs/MILESTONE5-FRONTEND-CHANGES.md` for this iteration's changes and validation boundary. All current UI workflows remain prototype-only and synthetic-data-only until authentication, RBAC, privacy approval and full operational acceptance are complete.


See `docs/MILESTONE5.1.5-INVITATION-SYNC-TIMING.md` for the current invitation-sync timing policy and `docs/VALIDATION-REPORT-MILESTONE5.1.5.md` for the latest validation boundary.


## Build 5.1.6 — ordered lifecycle recovery

When a server-backed synthetic visit has both local arrival and departure recorded but the Genetec check-in event is still Pending/Failed, visit details now selects the original check-in event as the next action. It explains that Genetec must independently verify check-in before checkout, permits replay of the saved check-in event after local checkout only when both original events exist and are ordered correctly, and refuses recovery if the event is older than the bridge's 30-minute new-event window. After the check-in is read-back verified, reopening visit details selects the original checkout event. Event IDs and timestamps are preserved; no synthetic timestamp is invented.

The lifecycle sync UI no longer applies the just-completed event's state to a newly rendered next-event button. This avoids disabling the checkout retry after a successful prerequisite check-in. The sequence remains separately confirmed native Genetec writes and should be exercised only with synthetic visits on an approved non-production Directory.


## Build 5.2.0 — gated student face-attendance prototype

Build 5.2.0 adds an optional Student attendance page and a local OpenCV-contrib LBPH matching service. It is disabled by default and requires both `STAMFORD_FACE_ATTENDANCE_ENABLED=1` and `STAMFORD_BIOMETRIC_POLICY_APPROVED=1`. Enrollment requires recorded student/guardian consent and an approval reference; samples must be individual captures, are encrypted with Windows DPAPI CurrentUser and are not stored as raw camera frames. Matching creates only a short-lived candidate ticket: staff must explicitly confirm every attendance event. Check-out cannot be recorded without a check-in in the same session.

Group event face matching has an additional `STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED=1` gate because identity matching from group photos is excluded from the current baseline unless separately approved. When enabled, matching is limited to a supplied roster of already enrolled/consented people; group images are not saved or used to enroll people; unmatched faces remain for manual reconciliation. This feature records into a local attendance ledger and does not modify Genetec access rights or create Genetec student attendance events.

The OpenCV-contrib recognizer is experimental. Distance is not a probability, no liveness/spoof defence is supplied, and threshold/false-match testing is mandatory before any controlled pilot. The service still lacks application authentication/RBAC; use synthetic identities only. See `docs/BIOMETRIC-ATTENDANCE-CHANGE-REQUEST.md`.


## Build 5.2.1 — Genetec Cardholder photo and credential-reference sync

The local loopback bridge now supports gated, read-only Cardholder search and retrieval of an individually assigned Cardholder portrait from Genetec. The VMS can combine that portrait with separate live individual camera captures during approved enrollment, retain the native Cardholder GUID and masked credential-reference metadata, and associate later human-confirmed attendance records with that Cardholder. It does not write attendance records or modify Genetec Cardholders, credentials, credential numbers, groups, or access rights.

The additional flag `STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED=1` is required in both the Python service and Genetec bridge processes, alongside the existing student face-attendance and biometric policy gates. All three remain off by default. Raw card numbers are masked by the bridge and rejected by the service if they are not masked. The selected Genetec portrait and camera captures are processed in memory; only encrypted face crops are retained using Windows DPAPI CurrentUser. A staff member must confirm every match before attendance is written to the local ledger.

See `docs/GENETEC-CARDHOLDER-FACE-SYNC.md`. This remains a gated prototype, not a production biometric attendance product. The precise Cardholder picture property and credential mapping must be verified against the installed Security Center 5.11 SDK on the target Windows host.
## Build 5.2.2 — Scoped Genetec student roster and staff-confirmed attendance

This incremental build adds an approval-gated, read-only bulk import of Genetec Cardholder metadata scoped by an explicit Student External ID prefix. The local roster stores Cardholder GUIDs, identifiers, names, portrait availability and masked credential references; it does not bulk-store portraits or raw credential values. A portrait is fetched in memory on demand for staff visual review. Attendance is recorded in the local VMS ledger only after staff confirm the selected record and person, and checkout requires a prior check-in in the same session.

The feature does not identify a person from an arbitrary image or write attendance to Genetec. Use an External ID prefix reserved for the approved student population; unfiltered exports are blocked. Because this remains prototype authentication/RBAC, use synthetic data only. See `docs/GENETEC-STUDENT-ROSTER-AND-ATTENDANCE.md`.


## Build 5.2.3 — dual-mode prototype integrations

The Integrations & devices page adds a persistent local mock SMS outbox, allow-list-gated live Vonage/Twilio SMS provider adapters, a Windows browser print-dialog check, and live browser webcam preview/frame capture. Mock sends are clearly marked `Simulated`; live provider acceptance is not represented as confirmed handset delivery. Live invitation SMS is blocked until an approved HTTPS visitor-pass page exists and `STAMFORD_PUBLIC_PASS_URL_TEMPLATE` points to it. The template setting alone does not deploy that endpoint. The UI build-title mismatch was corrected in build 5.2.3.

See [`docs/INTEGRATIONS-SETUP.md`](docs/INTEGRATIONS-SETUP.md) for setup, trial limitations, safe environment configuration and current acceptance boundaries. The webcam preview does not claim to implement or validate the SRS AI detectors. Windows printer output and the Genetec bridge still require target-host validation.


## Build 5.2.4 — kiosk face-attendance workflow

> Build 5.3.0 adds the Group Visits workflow; see `docs/GROUP-VISIT-WORKFLOW.md`.

The self-service kiosk now offers Student, Staff and Contractor attendance choices, a live browser-webcam capture, an explicit mock/live mode indicator, a returned candidate profile (name, record ID, affiliation and safe Genetec-linked metadata where present), a confirmation step, and a local attendance-session ledger. Mock mode returns scripted synthetic identities and is clearly labelled; it does not perform facial recognition or write to Genetec. Live mode is strictly gated and searches only active, individually approved profiles already linked to a Genetec Cardholder portrait and additional local enrollment samples. It does not yet enumerate and facial-search every raw Genetec Cardholder portrait. The experimental LBPH matcher is not a validated production-grade face identification model. See `docs/KIOSK-FACE-ATTENDANCE.md` and `docs/CHANGE-REQUEST-KIOSK-FACE-ATTENDANCE.md`; the new self-service identification purpose requires signed school safeguarding/privacy approval and a requirements change before real student data is used.


## Build 5.3.0 — Group Visits workflow

> Build 5.3.1 adds an SQLite-backed visitor-type catalogue with pre-populated categories, custom type management and a required invitation dropdown. See `docs/VISITOR-TYPES-CATALOGUE.md`.

The Group Visits view adds persistent group bookings with mixed Student/Guardian/Staff/Contractor/Other roster members, booking-scoped opaque QR passes, separate arrival/departure webcam capture metadata, annotated candidate preview, individually confirmed batch attendance and complete roster reconciliation. Mock mode uses clearly labelled scripted candidates; it does not analyse images. Live candidate matching is disabled by default and is scoped to approved individual profiles explicitly linked to the selected booking. Group photos are never enrolled as biometric templates and are not retained by default; optional retention requires explicit approval, expiry and Windows DPAPI protection.

This remains a local prototype without enterprise identity/SSO or production authorization hardening. Use synthetic data only, keep the service loopback-only, and do not enable live biometric matching or photo retention until Stamford signs off the expanded purpose, notices/consent, eligible populations, retention and access policy. The group flow does not implement automatic expected-vs-observed headcount comparison or validated large-group subgroup handling. See [`docs/GROUP-VISIT-WORKFLOW.md`](docs/GROUP-VISIT-WORKFLOW.md), [`docs/CHANGE-REQUEST-GROUP-VISIT.md`](docs/CHANGE-REQUEST-GROUP-VISIT.md), and [`docs/VALIDATION-REPORT-MILESTONE5.3.0-GROUP-VISIT.md`](docs/VALIDATION-REPORT-MILESTONE5.3.0-GROUP-VISIT.md).


## Build 5.3.2 — Stamford frontend branding

The provided Stamford American International School logo is now used in the persistent application sidebar, kiosk welcome screen, visitor sticker preview/print, Windows printer test label, group QR print page, and browser favicon. The source logo is stored under `ui/assets/`; a compact transparent crest is cropped from that same asset for the favicon. See [`docs/FRONTEND-BRANDING.md`](docs/FRONTEND-BRANDING.md).


## Build 5.4.0 — Reporting and multi-format exports

The Reports & exports workspace runs 17 report types from local data stores: visitor register, inside register, overdue/unresolved visits, daily/visitor-type/host summaries, visitor event log, visitor-type catalogue, group register, group-roster attendance, individual attendance, Genetec sync status, imported Genetec Cardholder roster, SMS delivery log, transactional audit log, and replacement-card and AI-event demo snapshots. Reports can be previewed and exported as CSV, Excel XLSX, JSON, PDF or standalone HTML. Shared filters are available, with date filtering disabled for demo snapshots that do not have reliable dates. Visitor email is excluded by default and can be added only for the Visitor Register.

Exports are server-generated from transactional source records where available, include source limitations, and omit QR tokens, raw credentials, portraits, face samples and group-photo bytes. CSV formula injection is mitigated and XLSX data uses string cells. Preview is capped at 200 rows and exports at 25,000 rows. The reports are operational prototype output, not a tamper-resistant enterprise audit log. See [`docs/REPORTING-EXPORTS.md`](docs/REPORTING-EXPORTS.md).


## Build 5.5.0 — Local accounts and feature permissions

- First launch requires one-time creation of the initial Administrator account; subsequent launches require username/password sign-in. Passwords are stored as PBKDF2-HMAC-SHA256 hashes with random salts, sessions expire after eight hours, login attempts are rate-limited, and authenticated writes require CSRF tokens.
- Built-in groups: Administrator, Security Operator, Receptionist, Host, Card Counter Operator, Group Organiser, Auditor and Read-only. Administrators can create accounts and custom permission groups, assign group membership, reset passwords, disable accounts, and allow/deny individual features for an account.
- Feature permissions are enforced by the local API (unknown API routes deny by default). Security-control-plane mutations are restricted to members of the built-in Administrator group; a last-administrator guard prevents accidental lockout.
- The initial Administrator account can be bootstrapped only while the account database is empty. Keep the service bound to loopback. This remains a prototype—not SSO/MFA, enterprise audit storage, or a production identity system. Use synthetic data only until security review and production IAM are approved.
- Authenticated dashboard snapshots are not written to browser local storage. Transactional APIs remain the authoritative source for visits, attendance and other records.

# Stamford VMS Prototype Repository

**Project:** ACOM × Stamford American International School — Visitor Management System
**Current milestone:** 5.1.6 — sequence pending historical check-in verification before checkout sync
**Repository status:** Prototype source, synthetic data, and a loopback-only .NET Framework bridge. Not a production release.

## What is in this repository

- `ui/` — Stamford-branded browser prototype. Visitor, kiosk, group, replacement-card and AI-review workflows remain demonstrative unless explicitly stated otherwise.
- `service/vms_service.py` — loopback-only HTTP service with SQLite snapshot persistence plus dedicated visitor-domain routes. `service/domain.py` implements transactional visit transitions and secure QR validation; neither service has production authentication/RBAC.
- `tests/` — automated regression tests for snapshot persistence, domain transitions, QR token handling, retry idempotency, and source/credential-store safety contracts.
- `docs/REQUIREMENTS-TRACEABILITY.csv`, `docs/IMPLEMENTATION-STATUS.md` and `docs/VALIDATION-REPORT.md` — requirement-by-requirement delivery status, validation evidence and remaining acceptance gates. See `docs/GENETEC-LOCAL-CREDENTIAL-SETUP.md` for saving bridge credentials once.
- `GenetecReadOnlyBridge/` — .NET Framework 4.8 bridge, using the locally installed Genetec Security Center 5.11 SDK. It binds only to `127.0.0.1:5190`.
- `docs/Genetec-Visitor-Field-Mapping.md` — current narrow mapping for invited synthetic bookings to a native Genetec Visitor.
- `DAY1-CLOSEOUT.md`, `VALIDATION-RESULTS.md` — implementation boundary and validation history.
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

The UI still has no application authentication or role-based authorization. This API is loopback-only and synthetic-data-only; it is a prototype domain API, not production-approved. Before real identities or public use, add approved authentication/authorization, rate limits, CSRF/threat controls for the final deployment, a transactional production data model, signed-off policies and the remaining acceptance evidence.

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

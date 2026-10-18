# Stamford VMS — Genetec Security Center 5.11 bridge

A loopback-only .NET Framework 4.8 bridge based on the connection/read-only query patterns in the supplied `Genetec.ReadOnlyPoc` and the `Engine.CreateEntity` pattern in the supplied Stamford sync application.

## Endpoints

- `GET /api/health` — bridge process health, listener mode and the current guarded write scopes (test Cardholder and native Visitor sync are reported separately).
- `GET /api/genetec/status` — logs in, reads the Directory version, checks the Visitor Management license flag and runs the cardholder-group configuration query. Reports the separately gated visitor-sync availability.
- `GET /api/genetec/capabilities` — reports SDK lifecycle support, the separate startup opt-in, and whether the complete operation mapping has been verified by successful native read-back. A reported SDK capability/opt-in does not by itself mean the mapping is accepted.
- `GET /api/genetec/visitor-booking-lookup?bookingId=VMS-SDKTEST-01` — performs a read-only Directory-backed exact-name lookup and returns matching Visitor GUIDs, so operators can inspect duplicates before any writes.
- `POST /api/genetec/test-cardholder` — optional, separately guarded create-only synthetic Cardholder test. It creates no credential and changes no rights.
- `POST /api/genetec/visitor-booking` — optional, separately guarded create/update for a VMS `Invited` synthetic booking using a native Genetec `Visitor`; requires Visitor Management licensing and a Directory version reported as 5.11. Uses a Directory-backed exact-name query before choosing create vs update; `Engine.GetEntities()` alone is cache-only and must not be used as the uniqueness check.

The listener is fixed to `127.0.0.1:5190`. It must not be exposed to the LAN or Internet.

## Build requirements

- Windows with .NET Framework 4.8 developer/targeting pack and Visual Studio Build Tools/MSBuild.
- Licensed Genetec Security Center 5.11 SDK compatible with the installed Directory build.
- `GSC_SDK` environment variable pointing to the folder containing `Genetec.Sdk.dll`.
- Approved least-privilege account. Status needs appropriate view/query permissions; native Visitor sync also needs Visitor Management licensed and privileges to create/update Visitors.

```powershell
$env:GSC_SDK = 'C:\Program Files (x86)\Genetec Security Center 5.11 SDK'
dotnet build .\GenetecReadOnlyBridge.csproj /p:Configuration=Debug
```

If `dotnet build` is unavailable for the .NET Framework project, build it in Visual Studio/MSBuild with the .NET Framework 4.8 developer pack installed. Do not copy source app `App.config` secrets into this project.

## One-time credential setup and bridge start

Provision the connection profile once with `Save-GenetecCredentials.ps1`. The profile is encrypted with Windows DPAPI in CurrentUser scope and stored under `%LOCALAPPDATA%\StamfordVMS\Secrets\genetec-connection.dpapi`; Windows ACLs restrict access to the current user and SYSTEM. It is tied to the same Windows user and machine. The SDK directory is stored in the same encrypted profile. If no profile exists, `Start-Bridge.ps1` fails immediately with setup instructions and does not prompt for secrets.

Normal bridge startup is **non-interactive**. Use `Start-Bridge.cmd` to launch with a double-click, or invoke `Start-Bridge.ps1` directly. Server, username, certificate value and password are loaded from the saved profile and supplied to the child process only while it runs, then cleared. Missing SDK/build prerequisites produce an immediate error instead of an interactive prompt.

The default is read-only. There are no startup confirmation prompts. For approved non-production testing using synthetic records, enable a scope explicitly:

- `Start-Bridge-VisitorSync.cmd` — one-click, non-interactive launch with invited Visitor create/update enabled.
- `Start-Bridge.cmd -EnableVisitorSync` — same invited Visitor create/update scope when using the general launcher.
- `Start-Bridge.cmd -EnableVisitorLifecycleSync` — guarded lifecycle scope for `VMS-SDKTEST-*` test-harness IDs or generated `VMS-UI-` + six uppercase hex digit UI IDs.
- `Start-Bridge-VisitorLifecycleTest.cmd` — one-click, non-interactive enablement of both invited Visitor upsert and guarded lifecycle sync for `VMS-SDKTEST-*` or prototype-generated `VMS-UI-` plus six hex digit IDs. Use only on an approved non-production Directory with synthetic data.
- `Start-Bridge.cmd -EnableTestCardholderWrites` — fixed synthetic test-cardholder scope.

Use a dedicated least-privilege SDK account instead of an administrator account. These switches only enable startup scopes; request-level confirmation, synthetic-booking restrictions, version/licence checks and SDK privilege checks remain enforced by the bridge API. Do not enable mutating scopes on production or with real student, staff, visitor or credential records.

## Native Visitor field mapping

See `../docs/Genetec-Visitor-Field-Mapping.md` for the prototype's mapping, idempotency rules, restrictions and known limitations.

- VMS booking ID `VMS-...` maps to a stable entity key `STAMFORD VMS BOOKING {BookingId}`. This lets retries find the same Visitor instead of creating one per retry.
- Visitor first/last name use native string properties; visit date, arrival and departure use native Visitor properties.
- The bridge verifies the entity key, names and local visit schedule by reading the Visitor back after a create/update.
- Host, destination, purpose, email, phone, QR token, credentials, cardholder groups, escorts and access rights are not mapped/changed.
- The booking-upsert endpoint accepts only `Invited` synthetic bookings. Native check-in/check-out uses a separate guarded lifecycle endpoint for `VMS-SDKTEST-*` test-harness records or exact `VMS-UI-` plus six hexadecimal character IDs generated by the prototype. The command-line lifecycle test harness remains restricted to `VMS-SDKTEST-*`. The implementation includes timestamp-provenance consistency checks, but fresh on-host build/read-back verification of the latest correction is still required before the lifecycle path is accepted.
- If the bridge response times out or read-back verification fails, inspect Genetec and the bridge console before retrying. Use the same booking ID for a safe retry.

## Read-only bridge smoke test

After starting the bridge, run this in a second PowerShell window to verify connection/authentication, Directory version, cardholder-group query, Visitor Management license and the visitor-sync startup opt-in. It sends no write requests:

```powershell
.\Test-BridgeReadOnly.ps1
```

The Genetec Bridge UI also updates the Day 1 integration gate to `Verified` or `Failed` based on the live response rather than keeping static pending labels.

## End-to-end synthetic test

Before retrying any visitor write, inspect the existing stable booking key using the new read-only diagnostic:

```powershell
.\Test-NativeVisitorLookup.ps1
```

The lookup performs a Directory-backed exact-name query for `STAMFORD VMS BOOKING {BookingId}`. If it returns more than one GUID, stop. An authorised Genetec administrator must inspect and reconcile the duplicate records before write tests resume. Because earlier test output already recorded two different GUIDs for `VMS-SDKTEST-01`, the test script also refuses to write under that known key if the new lookup unexpectedly reports zero matches.

After duplicates are reconciled and the bridge is running with native Visitor sync explicitly enabled, run the integration test in a second PowerShell window:

```powershell
.\Test-NativeVisitorSync.ps1
```

For a clean, different synthetic test booking after the lookup behavior has been verified, pass an unused key such as `-BookingId VMS-SDKTEST-02`. Do not reuse an ID that already has an unresolved duplicate.

The script preflights the booking key before writing. It submits the fixed synthetic booking (`VMS-SDKTEST-01`), retries the exact same payload, and then updates its name and visit window. All operations must return the same Visitor GUID; server-query, name and schedule read-back flags must pass. It requires an explicit confirmation phrase. Use only against the approved non-production environment.

`dotnet build` is supported for the current .NET Framework project when build prerequisites are installed. `dotnet run` may refuse to launch legacy .NET Framework projects even when the build succeeds; use `Start-Bridge.ps1` to start the built bridge.

For the UI flow, open `Start-Demo.cmd`, select **Genetec bridge → Test local bridge**, open an `Invited` synthetic visit and use **Sync invited booking to Genetec**.

## Security and production caveats

- Credentials are never stored in this project. Optional repeat-start convenience uses a per-user Windows DPAPI-encrypted profile outside the repository with an ACL restricted to the current user and SYSTEM. Secrets briefly reside in process memory/environment while the bridge runs. Replace the profile with `Save-GenetecCredentials.ps1` when rotating credentials.
- The browser only talks to the loopback listener, and the listener only allows origins from the local demo UI on port 8080.
- The bridge does not issue credentials, assign escorts or groups, change access rights, or send invitations.
- `Visitor.Name` is the VMS idempotency key for this prototype. The final human-facing name/key approach should be confirmed with Stamford before production rollout.
- This is not the full production integration: native check-in/checkout remains a guarded candidate pending live round-trip acceptance; host/destination field mapping, server-side retry/outbox across restarts, and the approved full audit/retention and operational framework remain outstanding.
- Actual Windows build and live SDK behavior must be verified on the intended 5.11 environment. Static checks in the development environment are not a substitute for that validation.


## Guarded native Visitor lifecycle (Day 2 candidate)

`POST /api/genetec/visitor-lifecycle` is disabled unless `GENETEC_ENABLE_VISITOR_LIFECYCLE_SYNC=1` was explicitly enabled at startup on an approved non-production Directory. It accepts only `VMS-SDKTEST-*` test harness bookings or strictly shaped prototype-generated `VMS-UI-XXXXXX` bookings, requires a separately confirmed request, validates the booking resolves to exactly one active native Visitor via a Directory query, checks the CheckInVisitors/CheckOutVisitors privilege, and stores stable event IDs and verified results in `%LOCALAPPDATA%\StamfordVMS\GenetecBridge\visitor-lifecycle-ledger.json`.

For check-in, the candidate SDK path calls `Visitor.Status.Activate(eventTime)` and sets `Visitor.Arrival` to the same event time, then requires a fresh SDK session to report `Status.ActivationType=SpecificDate` and a matching `Status.ActivationDate`. For checkout, it invokes `Visitor.Checkout()` and requires an archived `CheckedOut` record with native check-in and checkout timestamps. These mappings must be verified by `Test-NativeVisitorLifecycle.ps1` against a designated non-production Security Center 5.11 Directory before being treated as accepted. The capability flag `lifecycleOperationMappingVerified` is only set after both event types pass read-back. On the tested Directory, the active entity's raw `Visitor.CheckinDate` can mirror creation time even while Security Desk still offers **Check in**; lookup output therefore reports that field as `rawEntityCheckinDateUtc` for diagnostics, while canonical active check-in state is derived from `Status.ActivationType/ActivationDate`.

For a lifecycle test, create a fresh future synthetic appointment with `Test-NativeVisitorSync.ps1 -BookingId VMS-SDKTEST-06 -PrepareForLifecycleTest`. It starts ten minutes ahead to allow operator steps and avoid an arrival time already in the past. The lifecycle harness waits until expected arrival, then repeats a fresh read-only unique-GUID/state lookup before requesting the explicit write confirmation. Do not retry a write after ambiguous outcomes without inspecting Security Center first.

The bridge records only a synthetic lifecycle ledger. It does not create credentials, assign escorts/groups, or change access rights. A reported failure after a possible SDK mutation requires inspection of Security Center and reconciliation before retrying; do not switch to a new event ID to bypass a failed outcome.



Checkout read-back is treated as eventually consistent: after `Visitor.Checkout()` returns, the bridge polls `VisitorManager.GetArchivedVisitor(originalGuid)` for up to 15 seconds. Only the documented `SdkError.InvalidValue` (archive not visible yet) is retried; other SDK errors fail fast with a stage and SDK error code in the local bridge log. If checkout fails or read-back remains uncertain, do not replay automatically. Run `Test-NativeVisitorLifecycleState.ps1 -BookingId VMS-SDKTEST-06` or GET `/api/genetec/visitor-lifecycle-state?bookingId=VMS-SDKTEST-06` first. The endpoint is read-only and reports whether the Visitor is still active or is archived as checked out, alongside the durable event ledger. The lifecycle test also attempts this read-only inspection automatically after a checkout request error.

## Archived checkout ledger reconciliation

`GET /api/genetec/visitor-lifecycle-state` is read-only. If it confirms an archived `CheckedOut` visit while the saved check-out event ID is empty, use `Reconcile-NativeVisitorCheckout.ps1 -BookingId VMS-SDKTEST-06`. It prompts for an exact phrase and updates only the local event ledger after repeating the active/archive and check-in-ledger checks. It does not write to Genetec. Never use it unless the archive state is independently confirmed.
### Scoped student roster metadata import

`GET /api/genetec/student-roster?externalIdPrefix=STU-&limit=1000` is an approval-gated, read-only query. It returns only Cardholders whose supported Student/External ID field begins with the supplied prefix, enforces a 10,000-entity scan cap and a 1,000-result cap, returns masked credential references, and never returns portrait bytes. Do not use a prefix that also includes staff or visitors. It does not perform face identification or write attendance to Genetec.

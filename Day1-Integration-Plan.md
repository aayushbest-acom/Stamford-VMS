# Stamford VMS — integration milestone record

## Milestone A — read-only bridge

- Reuses the login and cardholder-group query patterns from the supplied apps.
- Reports the main Directory version, SDK assembly version, cardholder-group count, Visitor Management license state and explicit visitor-sync startup flag.
- Local API binds to `127.0.0.1:5190`; UI binds to `127.0.0.1:8080`.
- `System.Data` reference is required for SDK query result `DataTable` types.

## Milestone B — native Visitor booking sync

### Scope

- New endpoint: `POST /api/genetec/visitor-booking`.
- Only booking IDs matching `VMS-[A-Z0-9-]{1,24}` and VMS status `Invited` are allowed.
- Requires `GENETEC_ENABLE_VISITOR_SYNC=1`, the request confirmation string `SYNC_SYNTHETIC_STAMFORD_VMS_VISITOR`, a Directory version reported as 5.11, and `License.IsVisitorManagementEnabled`.
- Create/update native `Visitor` entity keyed by `STAMFORD VMS BOOKING {BookingId}`.
- Set/read back FirstName, LastName, VisitDate, Arrival and Departure; check the stable entity name and all schedule/name fields by read-back before reporting success.
- Return operation GUID, Genetec Visitor GUID, create/update outcome, idempotency key and verification flags. Log the operation GUID plus synthetic booking reference.
- Does not create credentials, assign escorts or groups, modify access rights, send SMS, or map host/destination/purpose/email/phone/QR fields.
- Reusing the same booking ID finds the same entity and updates it, supporting a guarded idempotent retry. Multiple matching entities fail closed.

### Test steps on the Windows SDK host

1. Build `GenetecReadOnlyBridge.csproj` with the exact Security Center 5.11-compatible SDK and .NET Framework 4.8 developer pack.
2. Start `Start-Bridge.ps1`; use the exact native Visitor sync opt-in phrase only on an approved non-production environment.
3. Query `GET /api/genetec/status`; verify `connected=true`, Directory version 5.11, `visitorManagementLicensed=true`, and `visitorSyncEnabled=true`.
4. In the UI, open an `Invited` synthetic booking, click **Sync invited booking to Genetec**, confirm and verify the returned GUID, operation ID and read-back flags.
5. Or run `Test-NativeVisitorSync.ps1`. It submits the fixed synthetic booking, retries the exact payload, then updates the name/window, and confirms that all three operations return the same Visitor GUID with query/name/schedule read-back verified.
6. Inspect the native Visitor in Genetec. If a call times out or read-back fails, investigate in Genetec and bridge logs before retrying.

## Known boundaries

- Actual build/runtime checks still depend on the Windows SDK host and target Directory.
- Prototype uses the entity name as its stable idempotency key. Confirm final name/key semantics and authoritative data ownership with Stamford before production.
- Only invited-booking create/update is in scope. Arrival/check-in and checkout lifecycle, host notifications, field ownership mapping, production retry queue/backoff, retention, security review and operations remain follow-on work.
- Native Visitor schedule setters are documented in the installed SDK XML as local dates/times converted to UTC; read-back verification compares local clock date/minute values. Verify this against the actual target time zone/DST configuration.

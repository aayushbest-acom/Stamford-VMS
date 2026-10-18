# Genetec Cardholder image and credential-reference sync (Build 5.2.1)

## Scope

This change adds a read-only bridge path for searching Genetec Cardholders and reading the individually assigned Cardholder portrait plus masked references to assigned credentials. The VMS uses the selected portrait as one enrollment sample and requires at least one separate, live individual camera sample. Matching and attendance recording remain in the local VMS face-attendance service. A confirmed attendance event retains the native Cardholder GUID and masked credential references as provenance.

It does **not** write attendance events, cardholders, credentials, groups, escorts, access rights, or credential values back to Genetec. The native Cardholder GUID is the stable link. Credential numbers are masked to the last four alphanumeric characters; raw credential numbers are never returned to the VMS UI or saved to its database.

## Read-only bridge endpoints

- `GET /api/genetec/cardholders?search=Name&limit=25` returns a bounded search result with Cardholder GUID, display name, available external-ID metadata, portrait availability and masked credential references.
- `GET /api/genetec/cardholder-photo?cardholderGuid=<GUID>` returns the selected portrait and the same credential references for local enrollment. It is served from the local bridge with `Cache-Control: no-store`; the bridge does not persist the picture.

The endpoints require all of these process flags:

- `STAMFORD_FACE_ATTENDANCE_ENABLED=1`
- `STAMFORD_BIOMETRIC_POLICY_APPROVED=1`
- `STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED=1`

The Python VMS service and Genetec bridge must both be restarted after setting flags. In PowerShell, flags can be set in the launch session after written policy approval:

```powershell
$env:STAMFORD_FACE_ATTENDANCE_ENABLED = '1'
$env:STAMFORD_BIOMETRIC_POLICY_APPROVED = '1'
$env:STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED = '1'
# Only after separate written approval for group-photo identity matching:
# $env:STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED = '1'
.\Start-Demo.cmd
```

These flags are development gates, not authentication. The prototype has no application login/RBAC; keep it bound to loopback and use synthetic data until production-grade access controls and signed approvals exist.

## Enrollment and attendance flow

1. An approved operator searches the Genetec directory by at least two characters and selects an exact Cardholder GUID.
2. The operator verifies the selected identity, approved eligibility, consent/notice record, and school approval reference.
3. The bridge reads the individually assigned Cardholder portrait. The user captures at least one additional individual camera sample. Each sample must contain exactly one face.
4. The VMS normalizes face crops in memory and encrypts them using Windows DPAPI CurrentUser before persisting them in the local SQLite biometric store. Raw images are not persisted; Genetec portrait bytes are only in memory during the request.
5. Matching returns a candidate ticket, not an attendance decision. An operator confirms the match before check-in/check-out is written. Checkout is blocked until a check-in exists for the same session/event.
6. Attendance is local to the VMS. Events preserve the Genetec Cardholder GUID and safe, masked credential references to support reconciliation.

## Credential handling

The bridge may inspect credential-number properties through reflection because SDK object models can vary by build, but only a masked suffix is returned (`••••1234`). The API never returns a raw credential number and never changes Genetec credential records or access rights. The Python service rejects unmasked credential identifiers before persistence. GUIDs and masked suffixes are linkage metadata, not proof that a credential was physically presented.

## Required limitations and acceptance

- Student biometric attendance must remain disabled until safeguarding/privacy approval and the institution's lawful notice/consent process are documented. Do not infer that a Cardholder portrait is, by itself, consent to biometric attendance.
- Face attendance is an experimental LBPH implementation; its distance is not a probability. Calibrate false-match/missed-match rates on approved validation data and do not use it for disciplinary, release or other consequential decisions.
- Group-photo identity matching is excluded unless Stamford approves a separate scope change. Even if enabled, only a supplied roster of individually enrolled people is compared; group photos are not used to enrol faces and raw group images are not persisted.
- Validate the exact picture representation and assigned credential API on the installed Security Center 5.11 SDK. The bridge uses bounded reflective property/method discovery because SDK object shapes can differ. No live SDK build has been claimed by static tests alone.
- Acceptance must include read-only Cardholder query, correct GUID/photo association, masked-only credential output, DPAPI at-rest test, duplicate GUID mapping rejection, consent/approval gates, human-confirmation enforcement, withdrawal/deletion and audit review.


## Bulk roster selection by Cardholder group

Build 5.2.3+ can load the read-only Genetec Cardholder group catalog and import only explicitly selected groups. Select only approved student-only groups, such as the school's configured divisions; leave Parent and Staff groups unselected. A verified External ID prefix is optional as an additional restriction. The bridge refuses an import without either an explicit group selection or a valid prefix; it never performs an unfiltered person export. Cardholder group mapping is reflected from the installed SDK and must be verified on the target Security Center 5.11 host before using the imported roster.

# Genetec Student Roster Import and Staff-Confirmed Attendance (incremental pilot)

## Purpose

This incremental addition imports the bounded set of Genetec Cardholders whose External ID starts with an explicitly provided student prefix. It stores only the Cardholder GUID, external ID, display name, portrait availability, masked credential references and import timestamp in the local VMS database. It does not store portrait bytes during bulk import.

The attendance test workflow deliberately does **not** identify a student by matching an arbitrary picture against the whole student database. The operator selects an imported record by external ID/name, loads that record’s assigned Genetec portrait on demand, visually compares the portrait with the person present, and confirms attendance. The confirmation and event are saved in the local VMS ledger only. No Genetec credential, access right or attendance record is changed.

## Scope and safety

- Bulk import requires an explicit `externalIdPrefix` and a maximum of 1–1000 records; it rejects unfiltered exports. The prefix must be one that is used only for the approved student population. The bridge scan is bounded to 10,000 Cardholders.
- Credential numbers are never returned in raw form; only masked references are stored.
- Portraits are fetched only for a Cardholder already present in the local roster. Image bytes are returned in memory, not saved to SQLite.
- Attendance requires a loaded portrait, a staff operator, a session ID, and explicit human confirmation. Checkout requires a prior check-in for the same Cardholder/session.
- These endpoints require the existing feature / school-approval / Cardholder-sync flags in both the VMS and bridge. Those flags are not authentication. The prototype still has no application login/RBAC; use synthetic data only until production access controls and school policy approval are complete.
- This path is a non-biometric, staff-confirmed fallback. It does not enable or validate facial identification.

## API

- `GET /api/genetec/student-roster?externalIdPrefix=STU-&limit=1000` — bridge bulk metadata query, prefix-filtered and read-only.
- `POST /api/v1/genetec/student-roster/import` — imports/updates the filtered metadata locally. Body: `{"externalIdPrefix":"STU-","limit":1000}`.
- `GET /api/v1/genetec/student-roster?search=...` — searches the locally imported roster.
- `GET /api/v1/genetec/student-roster/{cardholderGuid}/portrait` — on-demand portrait for staff review, never persisted.
- `POST /api/v1/genetec/student-roster/{cardholderGuid}/attendance` — staff-confirmed local check-in/out. Body includes `sessionId`, `operator`, `eventType`, `portraitViewed:true`, and `confirmIdentity:true`.
- `GET /api/v1/genetec/student-roster/attendance` — lists recorded local attendance events.

## Retest

Build the bridge on the approved Windows host against the installed Genetec SDK, run the Python test suite, then use a test-only prefix. Confirm roster count and GUID association, verify credential refs are masked, confirm no portrait bytes are stored, and test check-in then checkout in one session. Do not use group photos for enrollment or automated student identification.

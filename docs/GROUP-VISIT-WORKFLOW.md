# Group Visits — Build 5.3.0

## Scope delivered in the prototype

The Group Visits view now owns the booking and roster through the complete demonstration flow:

1. Create a booking with organisation, organiser, Stamford host, destination, purpose and visit window.
2. Add multiple roster members. Supported categories are Student, Guardian, Staff, Contractor and Other. The optional `guardianOf` field associates a guardian with a named student; an optional `faceProfileId` can link only an already active, approved individual enrollment of the same category.
3. Issue or rotate an opaque group QR pass. The service persists only the SHA-256 token digest. A group QR resolves the booking only; it does not identify a person or mark attendance.
4. Start the local webcam, capture a fresh arrival image and render a photo preview with candidate bounding boxes/labels.
5. In mock mode, scripted candidates and boxes support demonstrations but do not represent facial recognition. The result and confirmation screen clearly label this.
6. In live mode, matching is gated and uses only approved, individually enrolled profiles explicitly attached to this booking roster. A suggested candidate must be selected and human-confirmed. Unmatched, obscured or duplicate face candidates require manual reconciliation; there is no mock fallback when live matching is blocked.
7. Repeat with a fresh departure capture. A member cannot be checked out until that member has a recorded check-in.
8. Reconcile every roster member with an explicit disposition. The API rejects partial reconciliation and prevents a member with recorded attendance from being marked absent.

## API routes

- `GET /api/v1/group-visits/status`
- `GET /api/v1/group-visits`
- `GET /api/v1/group-visits/{groupEventId}`
- `POST /api/v1/group-visits`
- `POST /api/v1/group-visits/qr/validate`
- `POST /api/v1/group-visits/qr/svg`
- `POST /api/v1/group-visits/{groupEventId}/qr/rotate`
- `POST /api/v1/group-visits/{groupEventId}/roster`
- `POST /api/v1/group-visits/{groupEventId}/scan`
- `POST /api/v1/group-visits/{groupEventId}/confirm`
- `POST /api/v1/group-visits/{groupEventId}/reconcile`

Times in the booking are interpreted in Stamford school time (`Asia/Singapore`, UTC+08:00). The current 100-person roster cap and configured face-detection cap are prototype safety limits, not approved site capacity commitments.

## Configuration

- `STAMFORD_GROUP_VISIT_MODE=mock` is the default. Set to `live` only for approved live testing.
- Live matching additionally requires `STAMFORD_FACE_ATTENDANCE_ENABLED=1`, `STAMFORD_BIOMETRIC_POLICY_APPROVED=1`, and `STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED=1`, plus Windows DPAPI, the supported OpenCV-contrib face module, and the required individual profile samples.
- Genetec attendance-event submission has its own independent controls: `STAMFORD_GENETEC_ATTENDANCE_EVENT_SYNC_ENABLED=1` and `STAMFORD_GENETEC_ATTENDANCE_EVENT_SYNC_APPROVED=1`, as well as configured bridge/custom-event mapping. A `raised-unverified` result is not read-back proof; verify the corresponding event in the actual Genetec Directory.
- `STAMFORD_GROUP_PHOTO_RETENTION_APPROVED=1` and an approved `STAMFORD_GROUP_PHOTO_RETENTION_DAYS` between 1 and 3650 are required before the service stores photo bytes. On Windows, stored bytes are protected with DPAPI. The default is preview-only: the database stores capture timestamp, dimensions and SHA-256 digest, not the raw photo.

Do not turn on live biometric matching or retained group photographs until Stamford has approved the purpose, applicable notices/consent, eligible populations, retention schedule, access controls and fallback policy.

## Safety and limitations

- A group photograph is never used to create or update a biometric enrollment/template. Only previously enrolled profiles can be candidates.
- Live identity candidates are roster-scoped; this is not an unrestricted one-to-many search through every raw Genetec Cardholder portrait.
- Mock mode makes no recognition claim and does not write to Genetec.
- Live attendance is recorded in the prototype ledger and may emit the existing Genetec Custom Event outbox for linked Cardholders when that independently approved path is enabled. It is not a native Genetec attendance object; `raised-unverified` requires event read-back in Security Center.
- The application has no enterprise authentication or RBAC. Keep it loopback-only and use synthetic data until security work is accepted.
- SRS VMS-GRP-007 headcount comparison and the large-group camera/subgroup acceptance under VMS-GRP-008 are not implemented/validated. Do not treat a face-detection count as proof of who attended.

## Demonstration procedure

1. Start `Start-Demo.cmd` and open `http://127.0.0.1:8080`.
2. Select **Group visits → New group booking**. Enter the visit details and add one person per line, e.g.:
   - `Student | Ava Reed | STU-DEMO-01 | Grade 6A`
   - `Guardian | Olivia Reed | GDN-DEMO-01 | Parent | Ava Reed`
   - `Staff | Jordan Chen | STAFF-DEMO-02 | Visiting teacher`
3. Open **Photo + attendance**. In default mock mode, start the webcam, capture and match, select the scripted demo candidates, acknowledge the explicit confirmation text and confirm. This demonstrates the workflow only, not recognition accuracy.
4. Choose **Collective check-out**, capture a new image and confirm the test candidates. For a partial group, use **Reconcile full roster** and explicitly disposition all people.
5. For live testing, obtain the privacy/purpose approval, individually enroll eligible profiles, link their exact profile IDs to the booking, enable required gates and use a Windows test workstation. Start with synthetic or specifically approved test subjects and verify Genetec events independently.

## Regression checks

Run `python -m unittest discover -s tests -v`, compile the modified Python files with `python -m py_compile service/group_visits.py service/face_attendance.py service/vms_service.py`, and extract the inline UI script to run `node --check`. Target Windows/webcam/DPAPI/OpenCV/Genetec acceptance is additional to these automated checks.

# Change request: opt-in face attendance and approved group-roster matching

**Status: feature implemented behind gates; policy approval and on-host validation pending.**

## Why this is separate from the current VMS baseline

The current Stamford VMS SRS permits facial verification only for approved replacement-card purposes and says visiting children must not be biometrically enrolled by default. It also excludes identity recognition of every person in a group photo unless the scope/policy is separately approved. This feature therefore does not turn on automatically and does not redefine the baseline requirements.

## Feature behaviour

- Student attendance face matching is disabled by default. To enable the pilot, both `STAMFORD_FACE_ATTENDANCE_ENABLED=1` and `STAMFORD_BIOMETRIC_POLICY_APPROVED=1` must be present in the process environment, and an operator must provide individual consent/eligibility and school-approval references at enrollment.
- Face enrollment requires 2–5 separately captured, single-person images. A group photograph is explicitly rejected for enrollment.
- Preprocessed enrollment crops are encrypted by Windows DPAPI CurrentUser before being stored in a separate local SQLite database. This means the same Windows account on the same workstation is required to decrypt them. Raw camera captures are sent to the loopback API for processing, then discarded; they are not logged or persisted.
- Matching uses the experimental OpenCV-contrib LBPH recognizer. Its distance is not a probability or a validated accuracy score. The deployment threshold must be evaluated on approved representative test images, with false-match and missed-match rates measured before any controlled pilot.
- A match is only a candidate. An authorised staff member must explicitly confirm the candidate before a check-in or check-out event is written.
- Checkout requires a prior check-in in the same attendance session/event. Batch group confirmation is an all-or-nothing SQLite transaction.
- Group-photo identity matching is separately gated by `STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED=1` in addition to the student feature flags. It only compares detected faces against the pre-enrolled, consented IDs in the provided group roster. It does not create templates from the group photo, save the group photo, or automatically record unmatched attendees.
- Attendance events are stored in the local face-attendance ledger. This patch does not write student-attendance events or credentials to Genetec: the existing Genetec Visitor integration is not a substitute for the school's student-attendance system of record.
- Enrollment revocation deactivates the profile and deletes encrypted face samples. Attendance/audit history remains for approved retention and audit purposes; retention duration still needs a school decision.

## Local API

- `GET /api/v1/face-attendance/status`
- `GET /api/v1/face-attendance/students`
- `GET /api/v1/face-attendance/events?sessionId=...&groupEventId=...`
- `POST /api/v1/face-attendance/enroll`
- `POST /api/v1/face-attendance/students/{studentId}/revoke`
- `POST /api/v1/face-attendance/match`
- `POST /api/v1/face-attendance/confirm`
- `POST /api/v1/face-attendance/group-match`
- `POST /api/v1/face-attendance/group-confirm-batch`

The current application has no login or role-based authorization. The consent and approval references are prototype-level audit fields, not cryptographic proof of authorization. Do not enable this with real student data until authentication, RBAC, a signed privacy/safeguarding decision, retention/deletion policy, security review and UAT are complete.

## Approved test setup

Run on the approved Windows test workstation under the same Windows account that will own the local DPAPI profile. Install the optional OpenCV-contrib wheel in the application's Python environment; it replaces the standard `opencv-python-headless` wheel and should not coexist with it.

After the written approval and consent process are in place, start the app from a PowerShell session with:

```powershell
$env:STAMFORD_FACE_ATTENDANCE_ENABLED = '1'
$env:STAMFORD_BIOMETRIC_POLICY_APPROVED = '1'
# Only after separate written approval of group-photo identity matching:
$env:STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED = '1'
.\Start-Demo.cmd
```

Environment flags do not implement authentication; anyone with local process access can set them. Keep the service loopback-only and use synthetic identities during development.

## Acceptance tests still required on the school workstation

1. Verify DPAPI encrypt/decrypt and revocation under the intended Windows account.
2. Measure false matches and missed matches for the enrolled model using approved test captures; calibrate/approve the threshold before pilot use.
3. Validate camera lighting, pupil height, eyewear, masks, different days and replay/spoof scenarios; the current prototype does not provide robust liveness detection.
4. Verify check-in/out audit, staff confirmation, idempotency and retention/deletion on a backup/restore cycle.
5. For group pilot, verify roster scoping, no duplicate recognition, manual reconciliation for every unmatched face, and that group images are not written to disk/logs.
6. Obtain signed acceptance from Stamford safeguarding/privacy, IT/security and the accountable attendance owner before any production deployment.


## Build 5.2.1 addendum — Genetec Cardholder source linkage

A separate gated read-only import can search Genetec Cardholders and retrieve the individually assigned portrait plus masked credential-reference metadata. Enrollment uses that portrait as one reference sample and requires 1–4 additional individual camera samples. The native Cardholder GUID and masked credential references are stored as source linkage metadata on the local face enrollment and associated attendance events. Raw credential numbers are never returned; the bridge does not write attendance or credential changes to Genetec.

The additional `STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED=1` flag is required in both the VMS and bridge processes in addition to the student-face and biometric-policy flags. The local service verifies portrait GUID consistency, refuses raw/unmasked credential references, rejects duplicate Cardholder-to-student mappings, and requires the existing consent, approval and human-confirmation workflow. The Genetec picture-property/credential extraction is reflection-based to handle SDK version differences and must be validated on the installed 5.11 SDK. Detailed procedure: `docs/GENETEC-CARDHOLDER-FACE-SYNC.md`.

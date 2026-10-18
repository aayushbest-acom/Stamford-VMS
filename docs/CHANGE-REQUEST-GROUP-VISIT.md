# Change Request — Group Visit Photo and Collective Attendance

**Target prototype build:** 5.3.0
**Source baseline:** 5.2.4 kiosk face-attendance prototype
**State:** Prototype implementation added; user/policy and target-environment acceptance outstanding.

## Requested business capability

Allow an authorised organiser/operator to create a group visit with multiple students, guardians and other attendees; capture separate group arrival and departure images; associate suggested detections with the booking roster; batch-confirm individually reviewed attendance events; and reconcile missing, obscured or unmatched people.

## Traceability

- **VMS-GRP-001:** Persistent group booking, organiser/host, destination, purpose, school-local visit window, and mixed-person roster.
- **VMS-GRP-002:** Opaque group QR token, server-side validation, expiry, revocation and rotation. QR is a booking reference, never attendance proof.
- **VMS-GRP-003 / 005:** Separate arrival/departure scan IDs and UTC capture timestamps, image digest/dimensions, preview and retention gate.
- **VMS-GRP-004:** Annotated canvas with detection boxes, labels and explicit mock/live indicators.
- **VMS-GRP-006:** Required complete human roster reconciliation, no checkout-before-check-in, duplicate protection and audit events.
- **VMS-GRP-007:** Not implemented; requires detector/view validation and signed performance criteria.
- **VMS-GRP-008:** Site max size, subgroup process and camera framing remain open. The prototype's limit of 100 roster records is only a defensive software cap.
- **VMS-GRP-009 / SEC-014:** Group photos cannot enroll faces. Existing individually approved profile links are required for live candidate matching.

## Scope and design controls

Mock mode offers scripted candidates for UI rehearsal and does not analyse the image pixels. Live mode uses the approved local face matcher scoped to the exact group booking roster. Each candidate requires a human confirmation. It does not query every raw Genetec Cardholder portrait and does not create a native Genetec attendance object. For linked profiles, the existing separately gated Genetec Custom Event attendance path can be attempted and is marked `raised-unverified` until read-back is confirmed in Security Center.

Photographs remain preview-only by default. Optional local retention requires explicit purpose approval, a defined retention duration, and protection at rest using Windows DPAPI. Identity matching, group photo retention and guardian/minor handling require specific Stamford safeguarding/privacy approval before live deployment.

## Acceptance criteria for a later live pilot

1. Create/read/update test booking and exact roster on the local service; reject duplicate/invalid roster entries and expired/revoked QR passes.
2. Webcam captures and displays a fresh arrival image and a distinct departure image with UTC timestamp/correlation ID on the selected Windows hardware.
3. Live detections are restricted to active, approved individual profiles explicitly linked to that booking; unknown, duplicated, low-confidence and obscured people are not auto-attended.
4. Attendance changes only after an authorised human confirmation. Duplicate events, checkout without check-in, wrong-group tickets and partial reconciliation are rejected.
5. Evidence retention is preview-only unless a signed schedule enables protected, expiring Windows storage. Verify expiry and no group-image enrollment.
6. Genetec native Custom Events, where enabled, are independently read back in Security Center. A bridge submission response alone is insufficient.
7. Measure false matches, missed detections and performance for the agreed sample, view, lighting, group size and device. Sign off each supported condition separately.
8. Enforce approved authentication/RBAC, audit permissions, operational retention and accessibility/site requirements before real personal data is processed.

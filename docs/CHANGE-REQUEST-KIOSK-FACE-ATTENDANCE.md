# Change Request: Category-based Kiosk Face Attendance

**Change ID:** CR-VMS-KIOSK-FACE-ATTENDANCE-001  
**Baseline:** STAMFORD-VMS-SRS-001, Draft 1.1  
**Prototype target:** Build 5.2.4  
**Status:** Prototype flow implemented; live Genetec-wide gallery identification and formal policy approval remain open.

## Requested behaviour

At the kiosk, the person chooses Student, Staff or Contractor, starts a camera capture, reviews the proposed match and displayed identity attributes, and explicitly confirms a Check In or Check Out action. The application records an attendance event in the local prototype ledger and provides a session view of recorded events.

## Scope added by this change

- Category selection: Student, Staff, Contractor.
- Live browser webcam capture; camera frames are sent to the local service for the chosen adapter and are not saved by the kiosk capture route.
- Repeatable mock adapter with distinct synthetic profiles and an explicit simulated label.
- Gated live matcher against active, individually approved local face profiles linked to a Genetec Cardholder portrait and an additional locally captured sample.
- Candidate review with display name, person category, ID, affiliation/class, Cardholder GUID, masked credential reference and Cardholder groups where available.
- Explicit confirmation before an event is recorded.
- Separate Check In and Check Out event types; Check Out is rejected unless a matching Check In exists for the same person/category/session.
- Session-bound, short-lived, one-use match tickets; duplicate event protection; local audit trail.
- No write to Genetec attendance in this slice. API responses explicitly report `writtenToGenetec: false`.

## Relationship to the SRS baseline

The existing SRS, especially VMS-CARD-003 and SEC-010/SEC-011, scopes facial verification to the school-approved replacement-card workflow and requires biometric purpose/eligibility approval and non-biometric alternatives. The SRS also prohibits default biometric enrolment of visiting children (VMS-GRP-009 / SEC-014) and requires human safeguarding decisions (SEC-013).

This kiosk feature is **new biometric purpose and new attendance functionality**. It must be reviewed and signed off by Stamford's authorised business, IT/security, safeguarding/privacy and data owners before any live student, staff or contractor data is processed. Do not change the original SRS IDs to imply that the new scope was part of the approved baseline; retain this change request as a separately traceable item.

## Acceptance criteria

1. A visitor can select each of Student, Staff and Contractor, and the category is sent to the backend.
2. Mock mode labels itself as simulated, returns synthetic-only profile data, does not persist the camera frame, and cannot write to Genetec.
3. Live mode fails closed if approval flags, local encrypted storage or the face engine are unavailable; it never silently falls back to mock mode.
4. A live candidate must belong to the selected category and active, individually approved, Genetec-linked local gallery.
5. The UI presents returned, allow-listed profile attributes and masks credential references.
6. No attendance event is recorded without explicit confirmation.
7. Duplicate same-type events for the same person/session are rejected; Check Out without prior Check In is rejected.
8. Every event identifies its mode, person/category, session, event type, time, confirmation method and whether Genetec was changed.
9. A non-biometric/staff-assisted alternative is available in the approved operating procedure.
10. Formal live acceptance additionally requires accuracy/false-match testing, liveness/spoof evaluation, privacy review, role enforcement, target-host testing and signed acceptance thresholds.

## Important live-gallery limitation

The current code does **not** perform direct one-to-many face search against every picture stored in Genetec. The supported live path is a local approved gallery with individually linked Genetec portraits and additional camera samples. The read-only bridge can fetch a selected Cardholder's portrait and available profile metadata; a safely scoped roster can be imported through existing bridge paths, but this is not equivalent to a fully managed live gallery search. The latter requires an agreed approved group/prefix source, sync lifecycle, deletion/revocation rules, supported production matcher and performance/accuracy acceptance tests.

## Open decision

Stamford/ACOM must identify the source-of-truth mapping for the three categories: exact Genetec Cardholder Group names, a stable External ID prefix convention, or another approved attribute. Until signed off, the live gallery must not infer category from names or unverified heuristics.

# Kiosk Face Attendance — Prototype Guide

## Demonstration mode

Mock mode is the default. It demonstrates the full kiosk interaction but **does not recognize the captured face**. The camera is real; the returned identity is selected from synthetic data by the chosen category. It must remain labelled as simulated.

1. Start the application with `Start-Demo.cmd` on the development Windows machine.
2. Open the local kiosk page and choose **School attendance**.
3. Choose **Student**, **Staff** or **Contractor**.
4. Choose **Check In** or **Check Out**. Start the webcam if browser permission has not been granted.
5. Select **Scan face** and wait for the mock profile result.
6. Review the name, ID, class/affiliation, group and masked card reference; select the confirmation box and press **Confirm and mark arrival/departure**.
7. Review the current session's local attendance ledger. The demo banner states that Genetec has not been changed.
8. Check Out is intentionally rejected unless the same synthetic profile has a Check In for that session. Duplicate Check In/Check Out events are rejected.

The session ledger is demonstration data. Do not use real student/staff/contractor personal information in the mock flow.

## Adapter mode

`STAMFORD_KIOSK_FACE_MODE` supports:

- `mock` — default; synthetic identity and local demo ledger.
- `live` — approved local face gallery; fails closed if the live gates are not satisfied. It does not fall back to mock.

Do not enable live mode until Stamford has formally approved this new biometric attendance purpose, its population/eligibility rules, notice/consent process, retention and deletion schedule, fallback method, access policy and test plan. The original SRS does not approve unrestricted one-to-many face identification for general attendance by itself.

For an approved, isolated Windows test host, required flags are checked by the service. Configure only after the formal approvals are in place:

```powershell
$env:STAMFORD_KIOSK_FACE_MODE = 'live'
$env:STAMFORD_FACE_ATTENDANCE_ENABLED = '1'
$env:STAMFORD_BIOMETRIC_POLICY_APPROVED = '1'
$env:STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED = '1'
```

The application must also have the approved OpenCV-contrib face engine and Windows DPAPI-backed local sample protection. Use the same approved Windows user context for enrollment and recognition. The service still identifies itself as a prototype and currently has no application authentication/RBAC; do not put it on the school LAN or load real school data until that blocker is resolved.

## What live mode currently searches

Live matching searches only active, individually approved, consented local profiles for the selected category, linked to a Genetec Cardholder portrait and at least one additional individual camera sample. The local LBPH matcher is experimental; it has not been validated for school attendance, one-to-many biometric identification or anti-spoof/liveness. It is **not** a search of every raw portrait in Genetec.

The read-only Genetec Cardholder-photo response exposes the selected Cardholder's GUID, display name, first/last name, External ID where available, group names where the SDK exposes them, and masked credential references. These allow-listed fields are stored alongside the local enrollment to show identity context. Raw credential numbers are not returned. Do not assume the installed Genetec SDK exposes every field.

## Attendance write behaviour

Both demo and current live kiosk attendance events are stored in the prototype's local attendance ledger. The kiosk API currently returns `writtenToGenetec: false`; it does not create an attendance record in Genetec. A separate design is required to decide whether attendance belongs in VMS-only storage, a school information system, Genetec custom fields/events, or another authorised system. Do not imply that a local attendance record alters a physical access credential or enforcement policy.

## Live acceptance still required

- Agree exact Genetec Cardholder Groups or External ID convention for Student, Staff and Contractor.
- Define a scoped gallery sync plan, freshness, deactivation/deletion, and conflict resolution.
- Approve purpose, population, legal basis/consent, retention and non-biometric alternative.
- Add/enforce application authentication and role permissions.
- Select and validate the production-quality face matcher and presentation-attack/liveness controls.
- Measure false-match and missed-match rates with an approved, representative test protocol and thresholds.
- Test webcam positioning/lighting and operator failure flows on the target kiosk.
- Decide where attendance is authoritative and implement/read-back any required live write path.
- Perform live Genetec SDK build and acceptance on the actual supported Windows/Genetec installation.

## Troubleshooting

- **Camera denied:** use the local application URL, allow webcam permission in the browser, and confirm another app is not locking the camera.
- **Live mode not ready:** inspect `/api/v1/kiosk-face/status`; do not turn on approval flags merely to bypass the warning. Check approved feature gates, OpenCV-contrib and Windows encrypted storage prerequisites.
- **No candidate:** use staff-assisted attendance. Do not lower the model threshold casually or force a match.
- **Checkout blocked:** verify a Check In event exists for the same person and session.
- **Group missing:** a missing group name can mean the installed Genetec SDK did not expose group membership on the selected Cardholder.

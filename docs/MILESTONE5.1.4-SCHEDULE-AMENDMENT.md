# Stamford VMS — Milestone 5.1.4: Visit date default and schedule amendment

## Why the QR was rejected

The reported rejection showed school time `2026-10-17T12:32+08:00`, while the QR pass was scheduled for `2026-10-18`. The permitted window was `2026-10-18T12:01+08:00` through `2026-10-18T14:31+08:00`. That pass was for the following day, so the server correctly rejected check-in on the 17th. The confusion came from the invitation form defaulting the date to tomorrow.

## Changes

1. The invitation form defaults to **today in Asia/Singapore school time**. Operators can still choose a future date explicitly.
2. Active server-backed visits in `Invited` status now show **Edit visit date / time** in Visit details. The amendment API validates the date and window, retains the existing live QR token, recalculates token expiry, increments the row version and writes a before/after audit entry.
3. Checked-in, checked-out, cancelled or expired-token visits cannot be amended by this endpoint. A current active pass is required.
4. QR-window errors display the booked date/time, current school time and the permitted interval so tomorrow-versus-today mismatches are obvious.
5. If the visit has already been synchronized to Genetec, the operator must explicitly synchronize the updated schedule after amending it. This uses the existing booking identity and does not grant credentials/access rights.

## How to repair the pass shown in the screenshot

1. Open Visitor register and select the affected invited visit.
2. Click **Edit visit date / time**. Set the intended date (for a same-day visit, today's school-local date) and the correct start/end times, then save.
3. Reopen Visit details. If a Genetec Visitor GUID is present, choose **Sync invited booking to Genetec** again and confirm that schedule read-back is verified.
4. Re-scan the same QR. The active token is preserved and now validates against the amended schedule.

The VMS still intentionally rejects future-dated passes before their allowed check-in window opens. The check-in window remains 30 minutes before the scheduled start through 60 minutes after the scheduled end. Do not bypass this timing check.

## Validation boundary

Automated local regression tests pass 40/40, including rescheduling a pass and checking in with the same token during its amended window. Actual Windows/browser/Genetec runtime verification is still required. This prototype remains loopback-only and synthetic-data-only without application authentication/RBAC.

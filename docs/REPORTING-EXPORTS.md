# Reports and exports — Build 5.4.0

## Overview

The Reports & exports page reads server-side local records, presents a preview, and exports the same filtered dataset. It is a prototype reporting layer. It has no enterprise authentication/RBAC; keep the service bound to loopback and use synthetic/test data only.

- Preview: up to 200 rows.
- Export: up to 25,000 rows; the result metadata indicates if the export was capped.
- Formats: CSV (UTF-8 with BOM), Excel XLSX, JSON, PDF, and standalone HTML.
- Default privacy: no QR token/digest, raw credential number, face sample/embedding, portrait bytes, or group-photo bytes. Visitor email is available only when explicitly selected for Visitor Register.
- Filters: date range where source dates are reliable, status, visitor/person type, host/organiser, destination/area, group ID, session ID and text search. Unsupported date filters are rejected on demo snapshots rather than silently producing an empty report.

## Report catalogue

| ID | Report | Source and limitation |
|---|---|---|
| `visitor_register` | Visitor Register | Transactional visitor invitations and lifecycle state. Email is excluded unless explicitly requested. |
| `inside_register` | Current Inside Register | Recorded check-in with no recorded checkout; does not prove physical occupancy. |
| `overdue_visits` | Overdue / Unresolved Visits | Scheduled end plus configured 30–60 minute grace period (default 30 minutes). |
| `daily_visit_summary` | Daily Visit Summary | Filtered visit records grouped by visit date. |
| `visitor_type_summary` | Visitor Type Summary | Filtered visits grouped by the type recorded on each booking. |
| `host_summary` | Host / Destination Summary | Filtered visits grouped by host and destination. |
| `visitor_event_log` | Visitor Check-in / Checkout Events | Transactional QR lifecycle events; token values/digests are not exported. |
| `visitor_types_catalogue` | Visitor Types Catalogue | Server-side visitor-type definitions, including active/inactive state. |
| `group_visit_register` | Group Visit Register | Group booking, roster/event counts, lifecycle timestamps and reconciliation. No photos are exported. |
| `group_roster_attendance` | Group Roster Attendance | One record per roster member; excludes contact hints, face-profile IDs and photos. |
| `individual_attendance` | Individual Attendance Events | Confirmed local face attendance, Genetec-portrait review and mock kiosk events. Mock/local-only sources remain labelled. |
| `genetec_sync_status` | Genetec Attendance Sync Status | Outbox and group attendance synchronization state. `raised-unverified` is not a read-back-verified Genetec event. |
| `genetec_cardholder_roster` | Imported Genetec Cardholder Roster | Only scoped metadata already imported into the local roster; does not query every live Cardholder. No credentials/portraits are exported. |
| `sms_delivery_log` | SMS / Invitation Delivery Log | Masked recipients and provider outcome metadata; message bodies/full phone numbers are excluded. Provider acceptance does not guarantee handset delivery. |
| `system_audit_log` | Transactional System Audit Log | Available server-side audit/outbox records. This is not a tamper-resistant enterprise audit system. |
| `replacement_card_queue` | Replacement-card Queue (Demo Snapshot) | Browser/demo snapshot only; a transactional replacement-card service is not implemented. Reliable date filtering is unavailable. |
| `ai_event_review` | AI Event Review Queue (Demo Snapshot) | Browser/demo snapshot only; not evidence of live CCTV detection. Reliable date filtering is unavailable. |

## API

- `GET /api/v1/reports/catalog` — report definitions, supported formats, and row limits.
- `POST /api/v1/reports/preview` — accepts `{"reportId":"visitor_register","filters":{"fromDate":"YYYY-MM-DD","toDate":"YYYY-MM-DD","status":"Inside","visitorType":"Parent","host":"","destination":"","groupId":"","personType":"","sessionId":"","search":""},"includeContactDetails":false}`. Empty filter values can be omitted. `includeContactDetails` applies only to Visitor Register.
- `POST /api/v1/reports/export` — same request plus `"format":"csv|xlsx|json|pdf|html"`; returns a download with a generated filename and content type.

Errors use JSON with `ok:false`, `error`, and a bounded message. Unknown report IDs, invalid dates/ranges and unsupported formats fail closed.

## Export integrity and security

- CSV cell values that can trigger spreadsheet formulas are escaped.
- XLSX uses inline string cells rather than formulas; the workbook contains a worksheet, header style, auto-filter and frozen header row.
- HTML is standalone and all table content is escaped.
- PDF is standalone, paginated text/table output. Very long values are shortened for legibility; use CSV/XLSX/JSON for full field values.
- Files are generated from a fixed allow-list of columns; internal fields are not serialized.
- No export is signed or tamper evident. Keep generated reports within approved school access controls; this service does not have authenticated users or RBAC.

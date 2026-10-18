"""Server-side reporting and safe export for the Stamford VMS prototype.

Exports operational metadata only: no QR tokens, raw credential numbers, face
samples, or retained group-photo bytes are included. The service is loopback-only
and has no application authentication, so use synthetic data until RBAC is added.
"""
from __future__ import annotations

import csv
from contextlib import closing
from datetime import datetime, timedelta, timezone
import html
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import unicodedata
import zipfile
from xml.sax.saxutils import escape as xml_escape

UTC = timezone.utc
PREVIEW_LIMIT = 200
EXPORT_LIMIT = 25000
REPORT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUPPORTED_FORMATS = ("csv", "xlsx", "json", "pdf", "html")


class ReportingError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


REPORT_DEFINITIONS = [
    {"id": "visitor_register", "title": "Visitor Register", "description": "Invitations and recorded visit lifecycle; recorded status is not guaranteed physical occupancy.", "dateKey": "visitDate"},
    {"id": "inside_register", "title": "Current Inside Register", "description": "Visits with recorded check-in and no recorded checkout. This does not guarantee physical occupancy.", "dateKey": "visitDate"},
    {"id": "overdue_visits", "title": "Overdue / Unresolved Visits", "description": "Visits whose scheduled end plus configured grace period has passed without a recorded checkout.", "dateKey": "visitDate"},
    {"id": "daily_visit_summary", "title": "Daily Visit Summary", "description": "Daily visit counts by operational status.", "dateKey": "visitDate"},
    {"id": "visitor_type_summary", "title": "Visitor Type Summary", "description": "Invitation and lifecycle counts grouped by the type recorded on each booking.", "dateKey": "visitDate"},
    {"id": "host_summary", "title": "Host / Destination Summary", "description": "Visit counts grouped by host and destination.", "dateKey": "visitDate"},
    {"id": "visitor_event_log", "title": "Visitor Check-in / Checkout Events", "description": "Transactional QR visit events with prior and resulting status.", "dateKey": "occurredAtUtc"},
    {"id": "visitor_types_catalogue", "title": "Visitor Types Catalogue", "description": "Configured visitor types, including active/inactive state.", "dateKey": "updatedAtUtc"},
    {"id": "group_visit_register", "title": "Group Visit Register", "description": "Group bookings, expected/recorded member counts, arrival/departure and reconciliation state. No photos are exported.", "dateKey": "startsAtLocal"},
    {"id": "group_roster_attendance", "title": "Group Roster Attendance", "description": "One row per person on a group roster, including recorded attendance state and timestamps.", "dateKey": "startsAtLocal"},
    {"id": "individual_attendance", "title": "Individual Attendance Events", "description": "Confirmed individual attendance events from face-attendance, Genetec-portrait review and mock kiosk flows.", "dateKey": "occurredAtUtc"},
    {"id": "genetec_sync_status", "title": "Genetec Attendance Sync Status", "description": "Queued event submission status. raised-unverified is not a verified Genetec read-back.", "dateKey": "occurredAtUtc"},
    {"id": "genetec_cardholder_roster", "title": "Imported Genetec Cardholder Roster", "description": "Locally imported, scoped Cardholder metadata only; credentials and portraits are not exported.", "dateKey": "importedAtUtc"},
    {"id": "sms_delivery_log", "title": "SMS / Invitation Delivery Log", "description": "Provider request outcomes and masked recipients. Message bodies and full phone numbers are excluded.", "dateKey": "createdAtUtc"},
    {"id": "system_audit_log", "title": "Transactional System Audit Log", "description": "Server-side domain audit events. This is not a tamper-resistant enterprise security log.", "dateKey": "occurredAtUtc"},
    {"id": "replacement_card_queue", "title": "Replacement-card Queue (Demo Snapshot)", "description": "Prototype UI snapshot only; records are not currently stored in a transactional replacement-card service.", "dateKey": None},
    {"id": "ai_event_review", "title": "AI Event Review Queue (Demo Snapshot)", "description": "Prototype UI snapshot only; example events are not evidence of live CCTV detection.", "dateKey": None},
]

COLUMNS: dict[str, list[tuple[str, str]]] = {
    "visitor_register": [("bookingId", "Booking ID"), ("visitorName", "Visitor Name"), ("visitorType", "Visitor Type"), ("host", "Host"), ("destination", "Destination"), ("purpose", "Purpose"), ("visitDate", "Visit Date"), ("visitWindow", "Visit Window"), ("status", "Recorded Status"), ("createdAtUtc", "Created (UTC)"), ("checkedInAtUtc", "Check-in (UTC)"), ("checkedOutAtUtc", "Checkout (UTC)"), ("overdue", "Overdue Flag")],
    "inside_register": [("bookingId", "Booking ID"), ("visitorName", "Visitor Name"), ("visitorType", "Visitor Type"), ("host", "Host"), ("destination", "Destination"), ("visitDate", "Visit Date"), ("visitWindow", "Visit Window"), ("status", "Recorded Status"), ("checkedInAtUtc", "Check-in (UTC)"), ("minutesSinceScheduledEnd", "Minutes Past Scheduled End")],
    "overdue_visits": [("bookingId", "Booking ID"), ("visitorName", "Visitor Name"), ("visitorType", "Visitor Type"), ("host", "Host"), ("destination", "Destination"), ("visitDate", "Visit Date"), ("visitWindow", "Visit Window"), ("status", "Recorded Status"), ("checkedInAtUtc", "Check-in (UTC)"), ("minutesSinceScheduledEnd", "Minutes Past Scheduled End")],
    "daily_visit_summary": [("visitDate", "Visit Date"), ("total", "Total Visits"), ("invited", "Invited"), ("inside", "Recorded Inside"), ("overdue", "Overdue"), ("checkedOut", "Checked Out"), ("cancelled", "Cancelled")],
    "visitor_type_summary": [("visitorType", "Visitor Type"), ("total", "Total Visits"), ("invited", "Invited"), ("inside", "Recorded Inside"), ("overdue", "Overdue"), ("checkedOut", "Checked Out"), ("cancelled", "Cancelled")],
    "host_summary": [("host", "Host"), ("destination", "Destination"), ("total", "Total Visits"), ("invited", "Invited"), ("inside", "Recorded Inside"), ("overdue", "Overdue"), ("checkedOut", "Checked Out"), ("cancelled", "Cancelled")],
    "visitor_event_log": [("eventId", "Event ID"), ("bookingId", "Booking ID"), ("visitorName", "Visitor Name"), ("visitorType", "Visitor Type"), ("host", "Host"), ("destination", "Destination"), ("eventType", "Event Type"), ("occurredAtUtc", "Occurred (UTC)"), ("priorStatus", "Prior Status"), ("resultingStatus", "Resulting Status")],
    "visitor_types_catalogue": [("id", "Type ID"), ("name", "Visitor Type"), ("description", "Description"), ("active", "Active"), ("builtIn", "Built-in"), ("createdAtUtc", "Created (UTC)"), ("updatedAtUtc", "Updated (UTC)")],
    "group_visit_register": [("groupEventId", "Group ID"), ("organizationName", "Organization"), ("organiserName", "Organiser"), ("hostName", "Host"), ("destination", "Destination"), ("purpose", "Purpose"), ("startsAtLocal", "Starts (School Local)"), ("endsAtLocal", "Ends (School Local)"), ("status", "Status"), ("expectedCount", "Expected"), ("checkedInCount", "Check-ins Recorded"), ("checkedOutCount", "Check-outs Recorded"), ("arrivalAtUtc", "Arrival (UTC)"), ("departureAtUtc", "Departure (UTC)"), ("reconciledAtUtc", "Reconciled (UTC)")],
    "group_roster_attendance": [("groupEventId", "Group ID"), ("organizationName", "Organization"), ("displayName", "Person Name"), ("personType", "Person Type"), ("externalId", "External ID"), ("affiliation", "Affiliation"), ("guardianOf", "Guardian Of"), ("attendanceState", "Attendance State"), ("checkedInAtUtc", "Check-in (UTC)"), ("checkedOutAtUtc", "Checkout (UTC)"), ("lastReviewNote", "Reconciliation Note")],
    "individual_attendance": [("attendanceId", "Attendance ID"), ("personType", "Person Type"), ("personId", "Person ID"), ("displayName", "Person Name"), ("classOrAffiliation", "Class / Affiliation"), ("externalId", "External ID"), ("sessionId", "Session ID"), ("groupEventId", "Group ID"), ("eventType", "Event Type"), ("occurredAtUtc", "Occurred (UTC)"), ("operator", "Operator"), ("verificationMethod", "Verification Method"), ("source", "Source"), ("genetecSyncStatus", "Genetec Sync Status")],
    "genetec_sync_status": [("attendanceId", "Attendance ID"), ("cardholderGuid", "Cardholder GUID"), ("sessionId", "Session ID"), ("groupEventId", "Group ID"), ("eventType", "Event Type"), ("occurredAtUtc", "Occurred (UTC)"), ("confirmationMethod", "Confirmation Method"), ("syncStatus", "Sync Status"), ("attemptCount", "Attempts"), ("lastAttemptAtUtc", "Last Attempt (UTC)"), ("submittedAtUtc", "Submitted (UTC)"), ("lastErrorCode", "Last Error Code")],
    "genetec_cardholder_roster": [("cardholderGuid", "Cardholder GUID"), ("externalId", "External ID"), ("displayName", "Name"), ("firstName", "First Name"), ("lastName", "Last Name"), ("hasPortrait", "Portrait Available"), ("rosterScope", "Import Scope"), ("cardholderGroups", "Cardholder Groups"), ("importedAtUtc", "Imported (UTC)")],
    "sms_delivery_log": [("messageId", "Message ID"), ("createdAtUtc", "Created (UTC)"), ("updatedAtUtc", "Updated (UTC)"), ("provider", "Provider"), ("mode", "Mode"), ("recipient", "Recipient (Masked)"), ("purpose", "Purpose"), ("status", "Status"), ("providerMessageId", "Provider Message ID"), ("errorCode", "Error Code")],
    "system_audit_log": [("occurredAtUtc", "Occurred (UTC)"), ("subsystem", "Subsystem"), ("actor", "Actor"), ("action", "Action"), ("recordId", "Record ID"), ("outcome", "Outcome")],
    "replacement_card_queue": [("requestId", "Request ID"), ("applicantName", "Applicant"), ("studentId", "Student / Staff ID"), ("className", "Class / Department"), ("submitted", "Submitted"), ("verification", "Verification Scenario"), ("status", "Status")],
    "ai_event_review": [("eventId", "Event ID"), ("eventType", "Event Type"), ("camera", "Camera"), ("time", "Time"), ("area", "Area"), ("severity", "Severity"), ("source", "Source"), ("outcome", "Review Outcome")],
}


def _safe_text(value: object, max_length: int = 1000) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).replace("\x00", "")[:max_length]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None



def _connection(path: Path):
    conn = sqlite3.connect(str(path), timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _overdue_minutes(row: dict, grace: int) -> int | None:
    if row.get("checked_out_at_utc") or row.get("status") in ("Cancelled", "Checked out"):
        return None
    if row.get("status") not in ("Inside", "Overdue"):
        return None
    try:
        visit_date = str(row["visit_date"])
        window = str(row["visit_window"]).replace("–", "-").replace("—", "-")
        end = window.split("-")[-1].strip()
        end_hour, end_minute = [int(p) for p in end.split(":", 1)]
        scheduled = datetime.fromisoformat(visit_date).replace(hour=end_hour, minute=end_minute, tzinfo=timezone(timedelta(hours=8)))
        delta = datetime.now(UTC) - scheduled.astimezone(UTC)
        mins = int(delta.total_seconds() // 60) - grace
        return max(0, mins) if mins >= 0 else None
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


class ReportService:
    def __init__(self, main_db_path: Path, face_db_path: Path):
        self.main_db_path = Path(main_db_path)
        self.face_db_path = Path(face_db_path)

    @staticmethod
    def catalog() -> dict:
        # Demo snapshot rows carry human-readable labels rather than authoritative dates.
        # Tell clients not to offer a date filter for those two reports.
        return {"ok": True, "reports": [{**definition, "supportsDateFilter": definition["id"] not in {"replacement_card_queue", "ai_event_review"}, "supportedFormats": list(SUPPORTED_FORMATS)} for definition in REPORT_DEFINITIONS], "formats": list(SUPPORTED_FORMATS), "previewLimit": PREVIEW_LIMIT, "exportLimit": EXPORT_LIMIT, "privacy": "Exports omit QR tokens, raw credentials, face samples and photo bytes.", "prototypeOnly": True}

    def _snapshot(self) -> dict:
        try:
            with closing(_connection(self.main_db_path)) as conn:
                row = conn.execute("SELECT state_json FROM app_state WHERE state_key='prototype'").fetchone() if _table_exists(conn, "app_state") else None
            obj = json.loads(row["state_json"]) if row else {}
            return obj if isinstance(obj, dict) else {}
        except (sqlite3.Error, json.JSONDecodeError, OSError):
            return {}

    def _visitor_rows(self) -> list[dict]:
        try:
            with closing(_connection(self.main_db_path)) as conn:
                rows = conn.execute("""SELECT booking_id,visitor_name,visitor_email,host_name,destination,purpose,visit_date,visit_window,status,
                    created_at_utc,updated_at_utc,checked_in_at_utc,checked_out_at_utc,revoked_at_utc,visitor_type_id,visitor_type_name
                    FROM domain_visits ORDER BY visit_date DESC,created_at_utc DESC""").fetchall()
        except sqlite3.OperationalError:
            return []
        grace = int(os.environ.get("STAMFORD_VISIT_OVERDUE_GRACE_MINUTES", "30") or 30)
        if not 30 <= grace <= 60:
            grace = 30
        result = []
        for raw in rows:
            r = dict(raw)
            overdue = _overdue_minutes(r, grace)
            status = "Overdue" if overdue is not None else r["status"]
            item = {
                "bookingId": r["booking_id"], "visitorName": r["visitor_name"], "visitorTypeId": r["visitor_type_id"], "visitorType": r["visitor_type_name"],
                "host": r["host_name"], "destination": r["destination"], "purpose": r["purpose"], "visitDate": r["visit_date"], "visitWindow": r["visit_window"],
                "status": status, "createdAtUtc": r["created_at_utc"], "updatedAtUtc": r["updated_at_utc"], "checkedInAtUtc": r["checked_in_at_utc"],
                "checkedOutAtUtc": r["checked_out_at_utc"], "overdue": overdue is not None, "minutesSinceScheduledEnd": overdue,
            }
            item["_email"] = r["visitor_email"]
            result.append(item)
        return result

    @staticmethod
    def _validate_filters(raw: object) -> dict:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ReportingError(400, "INVALID_REPORT_FILTERS", "filters must be a JSON object.")
        clean = {}
        limits = {"fromDate": 10, "toDate": 10, "status": 64, "visitorType": 80, "host": 120, "destination": 120, "groupId": 60, "personType": 24, "sessionId": 100, "search": 120}
        for key, maximum in limits.items():
            value = raw.get(key, "")
            if value in (None, ""):
                continue
            if not isinstance(value, str) or len(value.strip()) > maximum:
                raise ReportingError(400, "INVALID_REPORT_FILTER", f"Filter {key} must be text of at most {maximum} characters.")
            value = value.strip()
            if key in ("fromDate", "toDate"):
                if not DATE_RE.fullmatch(value):
                    raise ReportingError(400, "INVALID_REPORT_DATE", f"Filter {key} must use YYYY-MM-DD.")
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError as exc:
                    raise ReportingError(400, "INVALID_REPORT_DATE", f"Filter {key} is not a valid calendar date.") from exc
            clean[key] = value
        if clean.get("fromDate") and clean.get("toDate") and clean["fromDate"] > clean["toDate"]:
            raise ReportingError(400, "INVALID_REPORT_DATE_RANGE", "fromDate must be on or before toDate.")
        return clean

    @staticmethod
    def _filter_rows(rows: list[dict], filters: dict, date_key: str) -> list[dict]:
        def contains(value: object, query: str) -> bool:
            return query.casefold() in _safe_text(value).casefold()
        out = []
        for row in rows:
            date_value = _safe_text(row.get(date_key))[:10]
            if filters.get("fromDate") and (not date_value or date_value < filters["fromDate"]):
                continue
            if filters.get("toDate") and (not date_value or date_value > filters["toDate"]):
                continue
            for filter_key, row_keys in (
                ("status", ("status", "eventType", "eventTypeLabel", "syncStatus", "outcome", "severity")),
                ("visitorType", ("visitorType", "personType")),
                ("host", ("host", "hostName", "organiserName", "actor")),
                ("destination", ("destination", "area")),
                ("groupId", ("groupEventId", "groupId")),
                ("personType", ("personType",)),
                ("sessionId", ("sessionId",)),
            ):
                query = filters.get(filter_key)
                if query and not any(contains(row.get(k), query) for k in row_keys):
                    break
            else:
                query = filters.get("search", "")
                if query and not any(contains(v, query) for k, v in row.items() if not k.startswith("_") and k != "visitorEmail"):
                    continue
                out.append(row)
        return out

    def _build_visitors(self, report_id: str, filters: dict, include_contact: bool) -> tuple[list[tuple[str, str]], list[dict], str]:
        base = self._visitor_rows()
        if report_id == "visitor_register":
            rows = self._filter_rows(base, filters, "visitDate")
            columns = list(COLUMNS[report_id])
            if include_contact:
                columns.insert(2, ("visitorEmail", "Visitor Email"))
            for row in rows:
                if include_contact:
                    row["visitorEmail"] = row.get("_email", "")
            return columns, rows, "Source: transactional local visitor database. Contact details are excluded unless explicitly requested."
        if report_id == "inside_register":
            rows = [r for r in base if r["checkedInAtUtc"] and not r["checkedOutAtUtc"] and r["status"] != "Cancelled"]
            rows = self._filter_rows(rows, filters, "visitDate")
            return COLUMNS[report_id], rows, "Inside means recorded check-in without recorded checkout; it is not proof of physical occupancy."
        if report_id == "overdue_visits":
            rows = [r for r in base if r.get("overdue")]
            rows = self._filter_rows(rows, filters, "visitDate")
            return COLUMNS[report_id], rows, "Overdue threshold uses STAMFORD_VISIT_OVERDUE_GRACE_MINUTES (30–60; default 30) after the scheduled end."
        if report_id in ("daily_visit_summary", "visitor_type_summary", "host_summary"):
            rows = self._filter_rows(base, filters, "visitDate")
            group_keys = {"daily_visit_summary": ("visitDate",), "visitor_type_summary": ("visitorType",), "host_summary": ("host", "destination")}[report_id]
            groups: dict[tuple, list[dict]] = {}
            for row in rows:
                key = tuple(row.get(k, "") for k in group_keys)
                groups.setdefault(key, []).append(row)
            result = []
            for key, items in sorted(groups.items(), key=lambda kv: tuple(str(x).casefold() for x in kv[0])):
                statuses = [x["status"] for x in items]
                record = {group_keys[i]: key[i] for i in range(len(group_keys))}
                record.update({"total": len(items), "invited": statuses.count("Invited"), "inside": sum(1 for x in items if x.get("checkedInAtUtc") and not x.get("checkedOutAtUtc") and x["status"] != "Overdue"),
                               "overdue": statuses.count("Overdue"), "checkedOut": statuses.count("Checked out"), "cancelled": statuses.count("Cancelled")})
                result.append(record)
            return COLUMNS[report_id], result, "Aggregated from filtered transactional visitor records; no physical-occupancy claim is implied."
        if report_id == "visitor_event_log":
            try:
                with closing(_connection(self.main_db_path)) as conn:
                    raw = conn.execute("""SELECT e.event_id,e.booking_id,e.action,e.occurred_at_utc,e.prior_status,e.resulting_status,
                        v.visitor_name,v.visitor_type_name,v.host_name,v.destination FROM domain_events e JOIN domain_visits v USING(booking_id)
                        ORDER BY e.occurred_at_utc DESC""").fetchall()
            except sqlite3.OperationalError:
                raw = []
            rows = [{"eventId": r["event_id"], "bookingId": r["booking_id"], "visitorName": r["visitor_name"], "visitorType": r["visitor_type_name"],
                     "host": r["host_name"], "destination": r["destination"], "eventType": r["action"], "occurredAtUtc": r["occurred_at_utc"],
                     "priorStatus": r["prior_status"], "resultingStatus": r["resulting_status"]} for r in raw]
            rows = self._filter_rows(rows, filters, "occurredAtUtc")
            return COLUMNS[report_id], rows, "Transactional QR lifecycle events only. Token digests are never exported."
        return [], [], ""

    def _group_register(self, filters: dict) -> tuple[list[tuple[str, str]], list[dict], str]:
        try:
            with closing(_connection(self.face_db_path)) as conn:
                rows = conn.execute("""SELECT g.*,(SELECT COUNT(*) FROM group_visit_roster r WHERE r.group_event_id=g.group_event_id) AS expected_count,
                    (SELECT COUNT(*) FROM group_visit_attendance a WHERE a.group_event_id=g.group_event_id AND a.event_type='CheckIn') AS checkin_count,
                    (SELECT COUNT(*) FROM group_visit_attendance a WHERE a.group_event_id=g.group_event_id AND a.event_type='CheckOut') AS checkout_count
                    FROM group_visits g ORDER BY g.starts_at_local DESC""").fetchall()
        except sqlite3.OperationalError:
            rows = []
        result = [{"groupEventId": r["group_event_id"], "organizationName": r["organization_name"], "organiserName": r["organiser_name"], "hostName": r["host_name"],
                   "destination": r["destination"], "purpose": r["purpose"], "startsAtLocal": r["starts_at_local"], "endsAtLocal": r["ends_at_local"], "status": r["status"],
                   "expectedCount": int(r["expected_count"]), "checkedInCount": int(r["checkin_count"]), "checkedOutCount": int(r["checkout_count"]),
                   "arrivalAtUtc": r["arrival_at_utc"], "departureAtUtc": r["departure_at_utc"], "reconciledAtUtc": r["reconciled_at_utc"]} for r in rows]
        result = self._filter_rows(result, filters, "startsAtLocal")
        return COLUMNS["group_visit_register"], result, "Group photographs and any photo-retention payloads are intentionally excluded. Counts represent per-person events."

    def _group_roster(self, filters: dict) -> tuple[list[tuple[str, str]], list[dict], str]:
        try:
            with closing(_connection(self.face_db_path)) as conn:
                rows = conn.execute("""SELECT r.*,g.organization_name,g.starts_at_local,
                    (SELECT MAX(a.occurred_at_utc) FROM group_visit_attendance a WHERE a.group_event_id=r.group_event_id AND a.member_id=r.member_id AND a.event_type='CheckIn') AS recorded_checkin,
                    (SELECT MAX(a.occurred_at_utc) FROM group_visit_attendance a WHERE a.group_event_id=r.group_event_id AND a.member_id=r.member_id AND a.event_type='CheckOut') AS recorded_checkout
                    FROM group_visit_roster r JOIN group_visits g USING(group_event_id) ORDER BY g.starts_at_local DESC,r.display_name COLLATE NOCASE""").fetchall()
        except sqlite3.OperationalError:
            rows = []
        result = [{"groupEventId": r["group_event_id"], "organizationName": r["organization_name"], "displayName": r["display_name"], "personType": r["person_type"],
                   "externalId": r["external_id"], "affiliation": r["affiliation"], "guardianOf": r["guardian_of"], "attendanceState": r["attendance_state"],
                   "checkedInAtUtc": r["recorded_checkin"] or r["checked_in_at_utc"], "checkedOutAtUtc": r["recorded_checkout"] or r["checked_out_at_utc"], "lastReviewNote": r["last_review_note"], "startsAtLocal": r["starts_at_local"]} for r in rows]
        result = self._filter_rows(result, filters, "startsAtLocal")
        return COLUMNS["group_roster_attendance"], result, "One row per roster member. Contact hints, face profile IDs and photos are excluded."

    def _individual_attendance(self, filters: dict) -> tuple[list[tuple[str, str]], list[dict], str]:
        result: list[dict] = []
        try:
            with closing(_connection(self.face_db_path)) as conn:
                if _table_exists(conn, "student_attendance_events"):
                    rows = conn.execute("""SELECT e.attendance_id,e.student_id,p.display_name,p.class_name,p.person_type,e.session_id,e.event_type,e.group_event_id,e.occurred_at_utc,e.operator_name,
                        e.genetec_cardholder_guid,COALESCE(o.sync_status,'not-linked') AS sync_status FROM student_attendance_events e JOIN face_enrollments p USING(student_id)
                        LEFT JOIN genetec_attendance_outbox o ON o.attendance_id=e.attendance_id ORDER BY e.occurred_at_utc DESC""").fetchall()
                    result.extend({"attendanceId": r["attendance_id"], "personType": r["person_type"], "personId": r["student_id"], "displayName": r["display_name"], "classOrAffiliation": r["class_name"],
                                   "externalId": "", "sessionId": r["session_id"], "groupEventId": r["group_event_id"], "eventType": r["event_type"], "occurredAtUtc": r["occurred_at_utc"],
                                   "operator": r["operator_name"], "verificationMethod": "approved individual face match + human confirmation", "source": "Face attendance", "genetecSyncStatus": r["sync_status"]} for r in rows)
                if _table_exists(conn, "genetec_cardholder_attendance_events"):
                    rows = conn.execute("SELECT * FROM genetec_cardholder_attendance_events ORDER BY occurred_at_utc DESC").fetchall()
                    result.extend({"attendanceId": r["attendance_id"], "personType": "Genetec Cardholder", "personId": r["cardholder_guid"], "displayName": r["display_name"], "classOrAffiliation": "", "externalId": r["external_id"],
                                   "sessionId": r["session_id"], "groupEventId": "", "eventType": r["event_type"], "occurredAtUtc": r["occurred_at_utc"], "operator": r["operator_name"],
                                   "verificationMethod": r["verification_method"], "source": "Genetec portrait review", "genetecSyncStatus": "local-event-recorded"} for r in rows)
                if _table_exists(conn, "kiosk_face_mock_attendance_events"):
                    rows = conn.execute("SELECT * FROM kiosk_face_mock_attendance_events ORDER BY occurred_at_utc DESC").fetchall()
                    result.extend({"attendanceId": r["attendance_id"], "personType": r["person_type"], "personId": r["person_id"], "displayName": r["display_name"], "classOrAffiliation": r["affiliation"], "externalId": r["external_id"],
                                   "sessionId": r["session_id"], "groupEventId": "", "eventType": r["event_type"], "occurredAtUtc": r["occurred_at_utc"], "operator": "", "verificationMethod": r["confirmation_method"],
                                   "source": "Kiosk face mock", "genetecSyncStatus": "local-only"} for r in rows)
        except sqlite3.OperationalError:
            pass
        result.sort(key=lambda r: r.get("occurredAtUtc") or "", reverse=True)
        result = self._filter_rows(result, filters, "occurredAtUtc")
        return COLUMNS["individual_attendance"], result, "Exports attendance metadata only; no images, face embeddings or raw credential references are included. Mock kiosk events remain labelled local-only."

    def _sync_status(self, filters: dict) -> tuple[list[tuple[str, str]], list[dict], str]:
        result: list[dict] = []
        try:
            with closing(_connection(self.face_db_path)) as conn:
                if _table_exists(conn, "genetec_attendance_outbox"):
                    rows = conn.execute("SELECT * FROM genetec_attendance_outbox ORDER BY occurred_at_utc DESC").fetchall()
                    result.extend({"attendanceId": r["attendance_id"], "cardholderGuid": r["cardholder_guid"], "sessionId": r["session_id"], "groupEventId": "", "eventType": r["event_type"],
                                   "occurredAtUtc": r["occurred_at_utc"], "confirmationMethod": r["confirmation_method"], "syncStatus": r["sync_status"], "attemptCount": r["attempt_count"],
                                   "lastAttemptAtUtc": r["last_attempt_at_utc"], "submittedAtUtc": r["submitted_at_utc"], "lastErrorCode": r["last_error_code"]} for r in rows)
                if _table_exists(conn, "group_visit_attendance"):
                    rows = conn.execute("SELECT a.*,r.display_name FROM group_visit_attendance a JOIN group_visit_roster r USING(member_id) ORDER BY a.occurred_at_utc DESC").fetchall()
                    result.extend({"attendanceId": r["attendance_id"], "cardholderGuid": "", "sessionId": r["group_event_id"], "groupEventId": r["group_event_id"], "eventType": r["event_type"],
                                   "occurredAtUtc": r["occurred_at_utc"], "confirmationMethod": r["confirmation_method"], "syncStatus": r["genetec_sync_status"], "attemptCount": 0, "lastAttemptAtUtc": "", "submittedAtUtc": "", "lastErrorCode": ""} for r in rows)
        except sqlite3.OperationalError:
            pass
        result = self._filter_rows(result, filters, "occurredAtUtc")
        return COLUMNS["genetec_sync_status"], result, "raised-unverified indicates the SDK accepted the raise call; independent Security Center read-back is still required."

    def _cardholder_roster(self, filters: dict) -> tuple[list[tuple[str, str]], list[dict], str]:
        try:
            with closing(_connection(self.face_db_path)) as conn:
                rows = conn.execute("SELECT * FROM genetec_cardholder_roster ORDER BY display_name COLLATE NOCASE").fetchall() if _table_exists(conn, "genetec_cardholder_roster") else []
        except sqlite3.OperationalError:
            rows = []
        result = []
        for r in rows:
            try:
                groups = json.loads(r["cardholder_groups_json"] or "[]")
                if not isinstance(groups, list): groups = []
            except json.JSONDecodeError:
                groups = []
            result.append({"cardholderGuid": r["cardholder_guid"], "externalId": r["external_id"], "displayName": r["display_name"], "firstName": r["first_name"], "lastName": r["last_name"],
                           "hasPortrait": bool(r["photo_available"]), "rosterScope": r["roster_prefix"], "cardholderGroups": "; ".join(_safe_text(g, 100) for g in groups[:50]), "importedAtUtc": r["imported_at_utc"]})
        result = self._filter_rows(result, filters, "importedAtUtc")
        return COLUMNS["genetec_cardholder_roster"], result, "Only locally imported scoped Cardholder metadata is present; this report does not query all live Genetec records."

    def _sms_log(self, filters: dict) -> tuple[list[tuple[str, str]], list[dict], str]:
        try:
            with closing(_connection(self.main_db_path)) as conn:
                rows = conn.execute("SELECT message_id,created_at_utc,updated_at_utc,provider,mode,recipient_display,purpose,status,provider_message_id,error_code FROM integration_sms_outbox ORDER BY created_at_utc DESC").fetchall()
        except sqlite3.OperationalError:
            rows = []
        result = [{"messageId": r["message_id"], "createdAtUtc": r["created_at_utc"], "updatedAtUtc": r["updated_at_utc"], "provider": r["provider"], "mode": r["mode"],
                   "recipient": r["recipient_display"], "purpose": r["purpose"], "status": r["status"], "providerMessageId": r["provider_message_id"], "errorCode": r["error_code"]} for r in rows]
        result = self._filter_rows(result, filters, "createdAtUtc")
        return COLUMNS["sms_delivery_log"], result, "Full message body, body preview and unmasked phone numbers are excluded. Provider accepted does not always mean handset delivered."

    def _audit_log(self, filters: dict) -> tuple[list[tuple[str, str]], list[dict], str]:
        result: list[dict] = []
        try:
            with closing(_connection(self.main_db_path)) as conn:
                if _table_exists(conn, "domain_audit"):
                    rows = conn.execute("SELECT occurred_at_utc,action,booking_id,outcome FROM domain_audit ORDER BY occurred_at_utc DESC").fetchall()
                    result.extend({"occurredAtUtc": r["occurred_at_utc"], "subsystem": "Visitor management", "actor": "Local service", "action": r["action"], "recordId": r["booking_id"] or "", "outcome": r["outcome"]} for r in rows)
                if _table_exists(conn, "integration_sms_outbox"):
                    rows = conn.execute("SELECT created_at_utc,message_id,status,error_code FROM integration_sms_outbox ORDER BY created_at_utc DESC").fetchall()
                    result.extend({"occurredAtUtc": r["created_at_utc"], "subsystem": "SMS integration", "actor": "Integration adapter", "action": "SmsOutboxUpdate", "recordId": r["message_id"], "outcome": f"{r['status']} {r['error_code']}".strip()} for r in rows)
        except sqlite3.OperationalError:
            pass
        try:
            with closing(_connection(self.face_db_path)) as conn:
                if _table_exists(conn, "group_visit_audit"):
                    rows = conn.execute("SELECT occurred_at_utc,action,group_event_id,actor,outcome FROM group_visit_audit ORDER BY occurred_at_utc DESC").fetchall()
                    result.extend({"occurredAtUtc": r["occurred_at_utc"], "subsystem": "Group visits", "actor": r["actor"], "action": r["action"], "recordId": r["group_event_id"], "outcome": r["outcome"]} for r in rows)
                if _table_exists(conn, "face_audit"):
                    rows = conn.execute("SELECT occurred_at_utc,action,student_id,session_id,outcome FROM face_audit ORDER BY occurred_at_utc DESC").fetchall()
                    result.extend({"occurredAtUtc": r["occurred_at_utc"], "subsystem": "Face attendance", "actor": "Prototype workflow", "action": r["action"], "recordId": r["student_id"] or r["session_id"] or "", "outcome": r["outcome"]} for r in rows)
        except sqlite3.OperationalError:
            pass
        result.sort(key=lambda r: r["occurredAtUtc"], reverse=True)
        result = self._filter_rows(result, filters, "occurredAtUtc")
        return COLUMNS["system_audit_log"], result, "Transactional subsystem events only; the browser-authored demo audit snapshot is excluded. Not a tamper-resistant enterprise log."

    def _snapshot_report(self, report_id: str, filters: dict) -> tuple[list[tuple[str, str]], list[dict], str]:
        snapshot = self._snapshot()
        state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else snapshot
        if report_id == "replacement_card_queue":
            result = []
            for x in (state.get("cards") or []) if isinstance(state.get("cards"), list) else []:
                if not isinstance(x, dict): continue
                result.append({"requestId": _safe_text(x.get("id")), "applicantName": _safe_text(x.get("name")), "studentId": _safe_text(x.get("studentId")), "className": _safe_text(x.get("className")),
                               "submitted": _safe_text(x.get("submitted")), "verification": _safe_text(x.get("verification")), "status": _safe_text(x.get("status"))})
            return COLUMNS[report_id], self._filter_rows(result, filters, "submitted"), "Demo/browser snapshot only. No transactional replacement-card request database exists yet."
        result = []
        for x in (state.get("events") or []) if isinstance(state.get("events"), list) else []:
            if not isinstance(x, dict): continue
            result.append({"eventId": _safe_text(x.get("id")), "eventType": _safe_text(x.get("type")), "camera": _safe_text(x.get("camera")), "time": _safe_text(x.get("time")), "area": _safe_text(x.get("area")), "severity": _safe_text(x.get("severity")), "source": _safe_text(x.get("source")), "outcome": _safe_text(x.get("outcome"))})
        return COLUMNS[report_id], self._filter_rows(result, filters, "time"), "Demo/browser snapshot only. These entries are not evidence of live CCTV analytics."

    def _generate(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ReportingError(400, "INVALID_REPORT_REQUEST", "Request body must be a JSON object.")
        report_id = payload.get("reportId")
        if not isinstance(report_id, str) or not REPORT_ID_RE.fullmatch(report_id):
            raise ReportingError(400, "INVALID_REPORT_ID", "Provide a valid reportId from the report catalogue.")
        definition = next((d for d in REPORT_DEFINITIONS if d["id"] == report_id), None)
        if definition is None:
            raise ReportingError(404, "REPORT_NOT_FOUND", "The requested report type is not available.")
        filters = self._validate_filters(payload.get("filters"))
        if (filters.get("fromDate") or filters.get("toDate")) and definition["id"] in {"replacement_card_queue", "ai_event_review"}:
            raise ReportingError(400, "DATE_FILTER_UNAVAILABLE", "Date filtering is not available for this demo-snapshot report because its source records do not contain reliable event dates.")
        include_contact = payload.get("includeContactDetails", False) is True
        if report_id in {"visitor_register", "inside_register", "overdue_visits", "daily_visit_summary", "visitor_type_summary", "host_summary", "visitor_event_log"}:
            columns, rows, note = self._build_visitors(report_id, filters, include_contact)
        elif report_id == "visitor_types_catalogue":
            try:
                with closing(_connection(self.main_db_path)) as conn:
                    raw = conn.execute("SELECT * FROM domain_visitor_types ORDER BY sort_order,display_name COLLATE NOCASE").fetchall()
            except sqlite3.OperationalError:
                raw = []
            rows = [{"id": r["visitor_type_id"], "name": r["display_name"], "description": r["description"], "active": bool(r["is_active"]), "builtIn": bool(r["is_builtin"]), "createdAtUtc": r["created_at_utc"], "updatedAtUtc": r["updated_at_utc"]} for r in raw]
            columns, note = COLUMNS[report_id], "Configured values from the server-side visitor-type catalogue."
            rows = self._filter_rows(rows, filters, "updatedAtUtc")
        elif report_id == "group_visit_register":
            columns, rows, note = self._group_register(filters)
        elif report_id == "group_roster_attendance":
            columns, rows, note = self._group_roster(filters)
        elif report_id == "individual_attendance":
            columns, rows, note = self._individual_attendance(filters)
        elif report_id == "genetec_sync_status":
            columns, rows, note = self._sync_status(filters)
        elif report_id == "genetec_cardholder_roster":
            columns, rows, note = self._cardholder_roster(filters)
        elif report_id == "sms_delivery_log":
            columns, rows, note = self._sms_log(filters)
        elif report_id == "system_audit_log":
            columns, rows, note = self._audit_log(filters)
        elif report_id in {"replacement_card_queue", "ai_event_review"}:
            columns, rows, note = self._snapshot_report(report_id, filters)
        else:
            raise ReportingError(404, "REPORT_NOT_FOUND", "The requested report type is not available.")
        # Internal/private fields are never included in exported columns or values.
        public_rows = [{key: row.get(key) for key, _ in columns} for row in rows]
        if len(public_rows) > EXPORT_LIMIT:
            public_rows = public_rows[:EXPORT_LIMIT]
            note += f" Export capped at {EXPORT_LIMIT} rows; refine filters for a complete extract."
        return {"ok": True, "reportId": report_id, "title": definition["title"], "description": definition["description"], "generatedAtUtc": _iso_now(),
                "filters": filters, "columns": [{"key": k, "label": label} for k, label in columns], "rows": public_rows, "rowCount": len(public_rows), "note": note,
                "truncated": len(rows) > EXPORT_LIMIT, "prototypeOnly": True}

    def preview(self, payload: object) -> dict:
        data = self._generate(payload)
        full_count = data["rowCount"]
        data["rows"] = data["rows"][:PREVIEW_LIMIT]
        data["previewLimit"] = PREVIEW_LIMIT
        data["previewTruncated"] = full_count > PREVIEW_LIMIT
        data["rowCount"] = full_count
        return data

    def export(self, payload: object) -> tuple[str, str, bytes, dict]:
        if not isinstance(payload, dict):
            raise ReportingError(400, "INVALID_REPORT_REQUEST", "Request body must be a JSON object.")
        fmt = payload.get("format", "csv")
        if not isinstance(fmt, str) or fmt.lower() not in SUPPORTED_FORMATS:
            raise ReportingError(400, "INVALID_EXPORT_FORMAT", f"format must be one of: {', '.join(SUPPORTED_FORMATS)}.")
        fmt = fmt.lower()
        data = self._generate(payload)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        slug = data["reportId"].replace("_", "-")
        filename = f"stamford-vms-{slug}-{stamp}.{fmt}"
        columns = data["columns"]
        rows = data["rows"]
        if fmt == "json":
            body = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif fmt == "csv":
            body = _to_csv(data).encode("utf-8-sig")
            content_type = "text/csv; charset=utf-8"
        elif fmt == "xlsx":
            body = _to_xlsx(data)
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif fmt == "pdf":
            body = _to_pdf(data)
            content_type = "application/pdf"
        else:
            body = _to_html(data).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        return filename, content_type, body, data


def _cell_value(value: object) -> str:
    return _safe_text(value, 4000)


def _to_csv(data: dict) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([c["label"] for c in data["columns"]])
    for row in data["rows"]:
        cells = []
        for col in data["columns"]:
            value = row.get(col["key"])
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                value = "'" + value
            cells.append(value if value is not None else "")
        writer.writerow(cells)
    return output.getvalue()


def _xl_col(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _to_xlsx(data: dict) -> bytes:
    cols = data["columns"]
    rows = data["rows"]
    sheet_rows = []
    sheet_rows.append('<row r="1">' + "".join(f'<c r="{_xl_col(i+1)}1" s="1" t="inlineStr"><is><t>{xml_escape(str(c["label"]))}</t></is></c>' for i, c in enumerate(cols)) + '</row>')
    for r_index, row in enumerate(rows, 2):
        cells = []
        for c_index, col in enumerate(cols, 1):
            value = row.get(col["key"])
            ref = f"{_xl_col(c_index)}{r_index}"
            if isinstance(value, bool):
                cells.append(f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>')
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                text = _cell_value(value)
                preserve = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t{preserve}>{xml_escape(text)}</t></is></c>')
        sheet_rows.append(f'<row r="{r_index}">' + "".join(cells) + '</row>')
    last_col = _xl_col(max(1, len(cols)))
    last_row = max(1, len(rows) + 1)
    worksheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                 '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                 '<sheetFormatPr defaultRowHeight="18"/><cols>' + ''.join(f'<col min="{i}" max="{i}" width="{min(36,max(12,len(str(c["label"]))+3))}" customWidth="1"/>' for i,c in enumerate(cols,1)) + '</cols>'
                 '<sheetData>' + ''.join(sheet_rows) + '</sheetData>'
                 f'<autoFilter ref="A1:{last_col}{last_row}"/>'
                 '</worksheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                     '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF112C46"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    with io.BytesIO() as buff:
        with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", root_rels)
            zf.writestr("xl/workbook.xml", workbook)
            zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            zf.writestr("xl/styles.xml", styles)
            zf.writestr("xl/worksheets/sheet1.xml", worksheet)
        return buff.getvalue()


def _html_cell(value: object) -> str:
    return html.escape(_cell_value(value), quote=True)


def _to_html(data: dict) -> str:
    cols = data["columns"]
    headers = "".join(f"<th>{_html_cell(c['label'])}</th>" for c in cols)
    body_rows = []
    for row in data["rows"]:
        body_rows.append("<tr>" + "".join(f"<td>{_html_cell(row.get(c['key']))}</td>" for c in cols) + "</tr>")
    filters = html.escape(json.dumps(data["filters"], ensure_ascii=False), quote=True)
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_html_cell(data['title'])}</title><style>body{{font:13px Arial,sans-serif;color:#182b3a;margin:28px}}h1{{color:#112c46}}.meta{{color:#526678;font-size:11px}}table{{border-collapse:collapse;width:100%;margin-top:18px}}th{{background:#112c46;color:#fff;text-align:left}}td,th{{border:1px solid #d8e1e8;padding:6px;vertical-align:top;overflow-wrap:anywhere}}tr:nth-child(even){{background:#f5f8fa}}.note{{padding:10px;background:#f3f7f9;margin-top:14px}}@media print{{body{{margin:12mm}}thead{{display:table-header-group}}tr{{page-break-inside:avoid}}}}</style></head><body>"
            f"<h1>{_html_cell(data['title'])}</h1><div class='meta'>Stamford VMS · Generated (UTC): {_html_cell(data['generatedAtUtc'])} · Rows: {data['rowCount']}</div>"
            f"<p>{_html_cell(data['description'])}</p><div class='meta'>Filters: {filters}</div><table><thead><tr>{headers}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
            f"<p class='note'>{_html_cell(data['note'])}</p><p class='meta'>Prototype report. Validate data provenance and authorization before operational use.</p></body></html>")


def _pdf_safe(value: object, limit: int = 140) -> str:
    text = _cell_value(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "replace").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:max(0, limit-1)] + "…"
        text = text.encode("ascii", "replace").decode("ascii")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _to_pdf(data: dict) -> bytes:
    # Dependency-free, paginated landscape PDF. Helvetica/WinAnsi means uncommon
    # glyphs are transliterated for portability; CSV/XLSX/JSON/HTML preserve UTF-8.
    page_w, page_h = 792, 612
    left, right, top = 36, 36, 570
    available_w = page_w - left - right
    cols = data["columns"]
    row_sets = [data["rows"][i:i+23] for i in range(0, max(1, len(data["rows"])), 23)] or [[]]
    page_contents: list[bytes] = []
    for page_index, page_rows in enumerate(row_sets, 1):
        commands = ["q", "0.07 0.17 0.27 rg", f"BT /F1 15 Tf {left} {top} Td ({_pdf_safe(data['title'], 100)}) Tj ET", "0 0 0 rg",
                    f"BT /F1 8 Tf {left} {top-16} Td (Generated UTC: {_pdf_safe(data['generatedAtUtc'], 40)} | Rows: {len(data['rows'])} | Page {page_index}/{len(row_sets)}) Tj ET",
                    f"BT /F1 8 Tf {left} {top-29} Td ({_pdf_safe(data['description'], 120)}) Tj ET"]
        ncols = max(1, len(cols))
        widths = [available_w / ncols] * ncols
        # Give ID/name columns a little more space and compact the rest.
        if ncols > 4:
            preferred = []
            for col in cols:
                k = col["key"].lower()
                preferred.append(1.45 if ("name" in k or "organization" in k or "destination" in k or "description" in k) else 1.0)
            total = sum(preferred)
            widths = [available_w * p / total for p in preferred]
        y = top - 48
        commands.append(f"0.88 0.92 0.95 rg {left} {y-3} {available_w} 20 re f")
        x = left
        for idx, col in enumerate(cols):
            label = _pdf_safe(col["label"], max(8, int(widths[idx] / 4.0)))
            commands.append(f"0.07 0.17 0.27 rg BT /F1 7 Tf {x+3:.2f} {y+4:.2f} Td ({label}) Tj ET")
            x += widths[idx]
        y -= 20
        for ri, row in enumerate(page_rows):
            if ri % 2 == 1:
                commands.append(f"0.97 0.98 0.99 rg {left} {y-3} {available_w} 19 re f")
            x = left
            for idx, col in enumerate(cols):
                cell = _pdf_safe(row.get(col["key"]), max(8, int((widths[idx]-6) / 3.6)))
                commands.append(f"0.08 0.13 0.18 rg BT /F1 6.5 Tf {x+3:.2f} {y+4:.2f} Td ({cell}) Tj ET")
                x += widths[idx]
            commands.append(f"0.82 0.86 0.89 RG 0.4 w {left} {y-3} m {left+available_w} {y-3} l S")
            y -= 19
        note = data["note"]
        commands.append(f"0.2 0.28 0.34 rg BT /F1 7 Tf {left} 32 Td ({_pdf_safe(note, 150)}) Tj ET")
        commands.append("Q")
        page_contents.append(("\n".join(commands)).encode("latin-1", "replace"))
    # Object 1 catalog, 2 pages, 3 font; then page/content pairs.
    objects: list[bytes] = []
    page_ids = [4 + i*2 for i in range(len(page_contents))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append((f"<< /Type /Pages /Kids [{' '.join(f'{pid} 0 R' for pid in page_ids)}] /Count {len(page_ids)} >>").encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, content in enumerate(page_contents):
        page_id = page_ids[i]
        stream_id = page_id + 1
        page = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>".encode("ascii")
        stream = b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream"
        objects.extend([page, stream])
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return bytes(out)

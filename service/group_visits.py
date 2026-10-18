"""Persistent group-visit booking, roster, photo-evidence metadata and reconciliation.

Group photographs are never used to enrol faces. Retained photographs are encrypted
with the configured protector (Windows DPAPI in the default deployment) and only
when the explicit retention approval and a retention duration are configured.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import uuid

try:
    from .face_attendance import FaceAttendanceError, _dpapi_protect, _dpapi_unprotect, _image_bytes, PERSON_TYPES
    from .qr_code import make_svg
except ImportError:  # direct script execution
    from face_attendance import FaceAttendanceError, _dpapi_protect, _dpapi_unprotect, _image_bytes, PERSON_TYPES
    from qr_code import make_svg

GROUP_ID_RE = re.compile(r"^GR-[A-Z0-9][A-Z0-9-]{2,39}$", re.I)
MEMBER_TYPES = {"Student", "Guardian", "Staff", "Contractor", "Other"}
MEMBER_STATES = {"Expected", "CheckedIn", "CheckedOut", "Absent", "Unresolved"}
EVENT_TYPES = {"CheckIn", "CheckOut"}
MAX_ROSTER = 100
MAX_GROUP_PHOTO_BYTES = 1_800_000
GROUP_QR_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
GROUP_LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Singapore")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean_text(value: object, label: str, max_length: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise GroupVisitError(400, "INVALID_FIELD", f"{label} must be text.")
    clean = "".join(ch for ch in value.strip() if ch >= " " and ch != "\x7f")[:max_length]
    if required and not clean:
        raise GroupVisitError(400, "FIELD_REQUIRED", f"{label} is required.")
    return clean


class GroupVisitError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


class GroupVisitStore:
    def __init__(self, db_path: Path, *, protector=None, unprotector=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._test_protector = protector is not None
        self._protect = protector or _dpapi_protect
        self._unprotect = unprotector or _dpapi_unprotect
        self._lock = threading.RLock()
        self._init_db()
        self.purge_expired_photos()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS group_visits (
                    group_event_id TEXT PRIMARY KEY,
                    organization_name TEXT NOT NULL,
                    organiser_name TEXT NOT NULL,
                    host_name TEXT NOT NULL DEFAULT '',
                    destination TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    starts_at_local TEXT NOT NULL,
                    ends_at_local TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Expected',
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    arrival_at_utc TEXT,
                    departure_at_utc TEXT,
                    reconciled_at_utc TEXT,
                    reconciliation_note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS group_qr_tokens (
                    token_hash TEXT PRIMARY KEY,
                    group_event_id TEXT NOT NULL REFERENCES group_visits(group_event_id) ON DELETE CASCADE,
                    expires_at_utc TEXT NOT NULL,
                    revoked_at_utc TEXT,
                    created_at_utc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_group_qr_token_group ON group_qr_tokens(group_event_id, expires_at_utc);
                CREATE TABLE IF NOT EXISTS group_visit_roster (
                    member_id TEXT PRIMARY KEY,
                    group_event_id TEXT NOT NULL REFERENCES group_visits(group_event_id) ON DELETE CASCADE,
                    display_name TEXT NOT NULL,
                    person_type TEXT NOT NULL,
                    external_id TEXT NOT NULL DEFAULT '',
                    affiliation TEXT NOT NULL DEFAULT '',
                    guardian_of TEXT NOT NULL DEFAULT '',
                    contact_hint TEXT NOT NULL DEFAULT '',
                    face_profile_id TEXT,
                    attendance_state TEXT NOT NULL DEFAULT 'Expected',
                    checked_in_at_utc TEXT,
                    checked_out_at_utc TEXT,
                    last_review_note TEXT NOT NULL DEFAULT '',
                    UNIQUE(group_event_id, face_profile_id)
                );
                CREATE INDEX IF NOT EXISTS idx_group_roster_group ON group_visit_roster(group_event_id, display_name);
                CREATE TABLE IF NOT EXISTS group_visit_scans (
                    scan_id TEXT PRIMARY KEY,
                    group_event_id TEXT NOT NULL REFERENCES group_visits(group_event_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL CHECK(event_type IN ('CheckIn','CheckOut')),
                    captured_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    image_sha256 TEXT NOT NULL,
                    image_width INTEGER NOT NULL,
                    image_height INTEGER NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('mock','live')),
                    photo_protected BLOB,
                    photo_retention_expires_at_utc TEXT,
                    photo_retention_status TEXT NOT NULL,
                    match_result_json TEXT NOT NULL DEFAULT '{}',
                    annotations_json TEXT NOT NULL DEFAULT '[]',
                    scan_status TEXT NOT NULL DEFAULT 'Captured'
                );
                CREATE INDEX IF NOT EXISTS idx_group_scans_booking ON group_visit_scans(group_event_id, captured_at_utc);
                CREATE TABLE IF NOT EXISTS group_visit_attendance (
                    attendance_id TEXT PRIMARY KEY,
                    group_event_id TEXT NOT NULL REFERENCES group_visits(group_event_id) ON DELETE CASCADE,
                    member_id TEXT NOT NULL REFERENCES group_visit_roster(member_id),
                    event_type TEXT NOT NULL CHECK(event_type IN ('CheckIn','CheckOut')),
                    occurred_at_utc TEXT NOT NULL,
                    operator_name TEXT NOT NULL,
                    confirmation_method TEXT NOT NULL,
                    match_ticket_id TEXT,
                    genetec_sync_status TEXT NOT NULL DEFAULT 'not-linked',
                    UNIQUE(member_id, group_event_id, event_type)
                );
                CREATE TABLE IF NOT EXISTS group_visit_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_event_id TEXT NOT NULL,
                    occurred_at_utc TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}'
                );
            """)
            # Correct a historic glyph typo if this table was created by an early prototype build.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(group_visit_attendance)")}
            if "genetec_sync_status" not in columns:
                conn.execute("ALTER TABLE group_visit_attendance ADD COLUMN genetec_sync_status TEXT NOT NULL DEFAULT 'not-linked'")

    def photo_retention_config(self) -> dict:
        approved = os.environ.get("STAMFORD_GROUP_PHOTO_RETENTION_APPROVED", "").strip().lower() in {"1", "true", "yes", "on"}
        try:
            days = int(os.environ.get("STAMFORD_GROUP_PHOTO_RETENTION_DAYS", "0"))
        except ValueError:
            days = 0
        if days < 1 or days > 3650:
            days = 0
        if not approved:
            state, reason = "preview-only", "Photo retention is disabled until Stamford approves the purpose and retention schedule."
        elif not days:
            state, reason = "preview-only", "Set an approved STAMFORD_GROUP_PHOTO_RETENTION_DAYS between 1 and 3650."
        elif os.name != "nt" and not self._test_protector:
            state, reason = "preview-only", "The default protected-photo store requires Windows DPAPI on the approved workstation."
        else:
            state, reason = "encrypted-retention-enabled", "Photographs will be encrypted before local persistence and expire under the configured retention period."
        return {"approved": approved, "retentionDays": days or None, "status": state, "reason": reason, "groupPhotoUsedForEnrollment": False}

    def status(self) -> dict:
        mode = os.environ.get("STAMFORD_GROUP_VISIT_MODE", "mock").strip().lower()
        if mode not in {"mock", "live"}:
            mode = "mock"
        retention = self.photo_retention_config()
        return {
            "ok": True,
            "mode": mode,
            "liveFaceMatchingApprovalFlag": os.environ.get("STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED", "").strip().lower() in {"1", "true", "yes", "on"},
            "photoRetention": retention,
            "photosEncryptedAtRest": retention["status"] == "encrypted-retention-enabled",
            "groupPhotoUsedForEnrollment": False,
            "humanConfirmationRequired": True,
            "authentication": "not-configured-prototype-only",
        }

    def _normalise_member(self, raw: object, group_id: str) -> dict:
        if not isinstance(raw, dict):
            raise GroupVisitError(400, "INVALID_ROSTER_MEMBER", "Every roster member must be an object.")
        kind = _clean_text(raw.get("personType", "Other"), "personType", 24, required=True)
        canonical = next((x for x in MEMBER_TYPES if x.casefold() == kind.casefold()), None)
        if canonical is None:
            raise GroupVisitError(400, "INVALID_PERSON_TYPE", "personType must be Student, Guardian, Staff, Contractor or Other.")
        name = _clean_text(raw.get("displayName"), "displayName", 120, required=True)
        external_id = _clean_text(raw.get("externalId"), "externalId", 80)
        affiliation = _clean_text(raw.get("affiliation"), "affiliation", 120)
        guardian_of = _clean_text(raw.get("guardianOf"), "guardianOf", 120)
        contact_hint = _clean_text(raw.get("contactHint"), "contactHint", 80)
        profile_id = _clean_text(raw.get("faceProfileId"), "faceProfileId", 64)
        if profile_id:
            with closing(self._connect()) as conn:
                row = conn.execute("SELECT person_type, active FROM face_enrollments WHERE student_id=?", (profile_id,)).fetchone()
            if not row or not row["active"]:
                raise GroupVisitError(400, "FACE_PROFILE_NOT_APPROVED", f"The linked face profile for {name} is not an active, approved enrollment.")
            if row["person_type"].casefold() != canonical.casefold():
                raise GroupVisitError(400, "FACE_PROFILE_TYPE_MISMATCH", f"The linked face profile category does not match {name}'s roster category.")
        return {
            "memberId": "GPM-" + secrets.token_hex(8).upper(),
            "groupEventId": group_id,
            "displayName": name,
            "personType": canonical,
            "externalId": external_id,
            "affiliation": affiliation,
            "guardianOf": guardian_of,
            "contactHint": contact_hint,
            "faceProfileId": profile_id or None,
            "attendanceState": "Expected",
            "checkedInAtUtc": None,
            "checkedOutAtUtc": None,
            "lastReviewNote": "",
        }

    def _booking_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
        members = conn.execute("SELECT * FROM group_visit_roster WHERE group_event_id=? ORDER BY rowid", (row["group_event_id"],)).fetchall()
        attendance = conn.execute("SELECT event_type,COUNT(*) AS n FROM group_visit_attendance WHERE group_event_id=? GROUP BY event_type", (row["group_event_id"],)).fetchall()
        counts = {r["event_type"]: int(r["n"]) for r in attendance}
        photo_rows = conn.execute("SELECT scan_id,event_type,captured_at_utc,image_width,image_height,photo_retention_status,scan_status,annotations_json FROM group_visit_scans WHERE group_event_id=? ORDER BY captured_at_utc", (row["group_event_id"],)).fetchall()
        roster = []
        for m in members:
            roster.append({
                "memberId": m["member_id"], "displayName": m["display_name"], "personType": m["person_type"],
                "externalId": m["external_id"], "affiliation": m["affiliation"], "guardianOf": m["guardian_of"],
                "contactHint": m["contact_hint"], "faceProfileId": m["face_profile_id"],
                "attendanceState": m["attendance_state"], "checkedInAtUtc": m["checked_in_at_utc"],
                "checkedOutAtUtc": m["checked_out_at_utc"], "lastReviewNote": m["last_review_note"],
                "faceMatchEligible": bool(m["face_profile_id"]),
            })
        photos = []
        for p in photo_rows:
            try: annotations = json.loads(p["annotations_json"] or "[]")
            except Exception: annotations = []
            photos.append({"scanId": p["scan_id"], "eventType": p["event_type"], "capturedAtUtc": p["captured_at_utc"], "imageWidth": p["image_width"], "imageHeight": p["image_height"], "photoRetentionStatus": p["photo_retention_status"], "scanStatus": p["scan_status"], "annotations": annotations})
        return {
            "groupEventId": row["group_event_id"], "organizationName": row["organization_name"],
            "organiserName": row["organiser_name"], "hostName": row["host_name"], "destination": row["destination"],
            "purpose": row["purpose"], "startsAtLocal": row["starts_at_local"], "endsAtLocal": row["ends_at_local"],
            "status": row["status"], "createdAtUtc": row["created_at_utc"], "updatedAtUtc": row["updated_at_utc"],
            "arrivalAtUtc": row["arrival_at_utc"], "departureAtUtc": row["departure_at_utc"],
            "reconciledAtUtc": row["reconciled_at_utc"], "reconciliationNote": row["reconciliation_note"],
            "expectedCount": len(roster), "checkedInCount": counts.get("CheckIn", 0), "checkedOutCount": counts.get("CheckOut", 0),
            "qrPassAvailable": bool(conn.execute("SELECT 1 FROM group_qr_tokens WHERE group_event_id=? AND revoked_at_utc IS NULL AND expires_at_utc>? LIMIT 1", (row["group_event_id"], _iso())).fetchone()),
            "roster": roster, "photoEvents": photos,
        }

    def create_booking(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise GroupVisitError(400, "INVALID_GROUP_BOOKING", "Request body must be a JSON object.")
        roster_raw = payload.get("rosterMembers")
        if not isinstance(roster_raw, list) or not 1 <= len(roster_raw) <= MAX_ROSTER:
            raise GroupVisitError(400, "INVALID_ROSTER", f"rosterMembers must contain 1–{MAX_ROSTER} people.")
        supplied_id = _clean_text(payload.get("groupEventId"), "groupEventId", 44).upper()
        if supplied_id and not GROUP_ID_RE.fullmatch(supplied_id):
            raise GroupVisitError(400, "INVALID_GROUP_ID", "groupEventId must use a GR-... identifier.")
        group_id = supplied_id or ("GR-" + secrets.token_hex(5).upper())
        organization = _clean_text(payload.get("organizationName"), "organizationName", 160, required=True)
        organiser = _clean_text(payload.get("organiserName"), "organiserName", 120, required=True)
        host = _clean_text(payload.get("hostName"), "hostName", 120)
        destination = _clean_text(payload.get("destination"), "destination", 160, required=True)
        purpose = _clean_text(payload.get("purpose"), "purpose", 240)
        starts = _clean_text(payload.get("startsAtLocal"), "startsAtLocal", 32, required=True)
        ends = _clean_text(payload.get("endsAtLocal"), "endsAtLocal", 32, required=True)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", starts) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", ends):
            raise GroupVisitError(400, "INVALID_VISIT_WINDOW", "Start and end must be local ISO datetimes in YYYY-MM-DDTHH:MM format.")
        try:
            if datetime.fromisoformat(ends) <= datetime.fromisoformat(starts):
                raise ValueError
        except ValueError as exc:
            raise GroupVisitError(400, "INVALID_VISIT_WINDOW", "The group visit end must be later than its start.") from exc
        normalised = [self._normalise_member(m, group_id) for m in roster_raw]
        profiles = [m["faceProfileId"] for m in normalised if m["faceProfileId"]]
        if len(profiles) != len(set(profiles)):
            raise GroupVisitError(400, "DUPLICATE_FACE_PROFILE", "Each approved face profile may appear only once in a group roster.")
        now = _iso()
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("INSERT INTO group_visits(group_event_id,organization_name,organiser_name,host_name,destination,purpose,starts_at_local,ends_at_local,status,created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (group_id, organization, organiser, host, destination, purpose, starts, ends, "Expected", now, now))
                self._insert_members(conn, group_id, normalised)
                qr_token = secrets.token_urlsafe(32)
                end_local = datetime.fromisoformat(ends).replace(tzinfo=GROUP_LOCAL_TZ)
                qr_expiry = _iso(end_local.astimezone(timezone.utc) + timedelta(hours=2))
                conn.execute("INSERT INTO group_qr_tokens(token_hash,group_event_id,expires_at_utc,created_at_utc) VALUES(?,?,?,?)", (hashlib.sha256(qr_token.encode("utf-8")).hexdigest(), group_id, qr_expiry, now))
                self._audit(conn, group_id, "GroupBookingCreated", organiser, "Success", {"rosterCount": len(normalised), "linkedFaceProfiles": len(profiles), "groupQrIssued": True})
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise GroupVisitError(409, "GROUP_BOOKING_ALREADY_EXISTS", "A group booking with that identifier already exists.") from exc
            except Exception:
                conn.execute("ROLLBACK")
                raise
        result = self.get_booking(group_id)
        result["groupQrToken"] = qr_token  # shown only at issuance; the raw token is never persisted
        return result

    def _insert_members(self, conn: sqlite3.Connection, group_id: str, members: list[dict]) -> None:
        for item in members:
            conn.execute("INSERT INTO group_visit_roster(member_id,group_event_id,display_name,person_type,external_id,affiliation,guardian_of,contact_hint,face_profile_id,attendance_state) VALUES(?,?,?,?,?,?,?,?,?,'Expected')", (item["memberId"], group_id, item["displayName"], item["personType"], item["externalId"], item["affiliation"], item["guardianOf"], item["contactHint"], item["faceProfileId"]))

    def add_roster(self, group_id: str, payload: object) -> dict:
        group_id = self._validate_group_id(group_id)
        members_raw = payload.get("rosterMembers") if isinstance(payload, dict) else None
        if not isinstance(members_raw, list) or not 1 <= len(members_raw) <= MAX_ROSTER:
            raise GroupVisitError(400, "INVALID_ROSTER", f"rosterMembers must contain 1–{MAX_ROSTER} people.")
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT status,arrival_at_utc FROM group_visits WHERE group_event_id=?", (group_id,)).fetchone()
        if row is None:
            raise GroupVisitError(404, "GROUP_NOT_FOUND", "Group booking was not found.")
        if row["arrival_at_utc"] or row["status"] not in {"Expected", "Roster incomplete"}:
            raise GroupVisitError(409, "ROSTER_LOCKED", "The roster is locked after group arrival. Use authorised reconciliation for post-arrival exceptions.")
        normalised = [self._normalise_member(m, group_id) for m in members_raw]
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._insert_members(conn, group_id, normalised)
                conn.execute("UPDATE group_visits SET updated_at_utc=? WHERE group_event_id=?", (_iso(), group_id))
                self._audit(conn, group_id, "GroupRosterUpdated", "Prototype operator", "Success", {"addedCount": len(normalised)})
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise GroupVisitError(409, "DUPLICATE_ROSTER_PROFILE", "A person/profile is already present in the group roster.") from exc
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_booking(group_id)

    @staticmethod
    def _validate_group_id(group_id: object) -> str:
        if not isinstance(group_id, str) or not GROUP_ID_RE.fullmatch(group_id.strip()):
            raise GroupVisitError(400, "INVALID_GROUP_ID", "Provide a valid group event ID.")
        return group_id.strip().upper()

    def list_bookings(self) -> dict:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM group_visits ORDER BY created_at_utc DESC").fetchall()
            items = [self._booking_dict(conn, row) for row in rows]
        return {"ok": True, "count": len(items), "groups": items, "mode": self.status()["mode"]}

    def get_booking(self, group_id: str) -> dict:
        group_id = self._validate_group_id(group_id)
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM group_visits WHERE group_event_id=?", (group_id,)).fetchone()
            if row is None:
                raise GroupVisitError(404, "GROUP_NOT_FOUND", "Group booking was not found.")
            return self._booking_dict(conn, row)

    def rotate_qr(self, group_id: str) -> dict:
        group_id = self._validate_group_id(group_id)
        token = secrets.token_urlsafe(32)
        now = _iso()
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT ends_at_local,status FROM group_visits WHERE group_event_id=?", (group_id,)).fetchone()
                if not row:
                    raise GroupVisitError(404, "GROUP_NOT_FOUND", "Group booking was not found.")
                if row["status"] == "Cancelled":
                    raise GroupVisitError(409, "GROUP_CANCELLED", "A QR pass cannot be issued for a cancelled group booking.")
                conn.execute("UPDATE group_qr_tokens SET revoked_at_utc=? WHERE group_event_id=? AND revoked_at_utc IS NULL", (now, group_id))
                end_local = datetime.fromisoformat(row["ends_at_local"]).replace(tzinfo=GROUP_LOCAL_TZ)
                expiry = _iso(end_local.astimezone(timezone.utc) + timedelta(hours=2))
                conn.execute("INSERT INTO group_qr_tokens(token_hash,group_event_id,expires_at_utc,created_at_utc) VALUES(?,?,?,?)", (hashlib.sha256(token.encode("utf-8")).hexdigest(), group_id, expiry, now))
                self._audit(conn, group_id, "GroupQrRotated", "Prototype operator", "Success", {})
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"ok": True, "groupEventId": group_id, "groupQrToken": token, "expiresAtUtc": expiry, "tokenStoredAs": "SHA-256 hash only"}

    def validate_qr(self, token: object) -> dict:
        if not isinstance(token, str) or not GROUP_QR_TOKEN_RE.fullmatch(token):
            raise GroupVisitError(400, "INVALID_GROUP_QR", "The group QR token format is invalid.")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT t.expires_at_utc,t.revoked_at_utc,g.*,(SELECT COUNT(*) FROM group_visit_roster r WHERE r.group_event_id=g.group_event_id) AS expected_count FROM group_qr_tokens t JOIN group_visits g USING(group_event_id) WHERE t.token_hash=?", (digest,)).fetchone()
        if not row:
            raise GroupVisitError(404, "GROUP_QR_NOT_FOUND", "This group QR pass is unknown or has been revoked.")
        if row["revoked_at_utc"]:
            raise GroupVisitError(410, "GROUP_QR_REVOKED", "This group QR pass has been revoked.")
        if row["expires_at_utc"] <= _iso():
            raise GroupVisitError(410, "GROUP_QR_EXPIRED", "This group QR pass has expired.")
        if row["status"] == "Cancelled":
            raise GroupVisitError(410, "GROUP_CANCELLED", "This group booking has been cancelled.")
        return {"ok": True, "valid": True, "groupEventId": row["group_event_id"], "organizationName": row["organization_name"], "organiserName": row["organiser_name"], "destination": row["destination"], "startsAtLocal": row["starts_at_local"], "endsAtLocal": row["ends_at_local"], "status": row["status"], "expectedCount": int(row["expected_count"]), "expiresAtUtc": row["expires_at_utc"], "tokenStoredAs": "SHA-256 hash only", "groupQrAuthorisesAttendance": False, "message": "Group pass identifies the booking only. It does not confirm any person's identity or attendance."}

    def qr_svg(self, token: object) -> bytes:
        self.validate_qr(token)
        return make_svg(str(token))

    def _validate_scan_state(self, group_id: str, event_type: str) -> sqlite3.Row:
        group_id = self._validate_group_id(group_id)
        if event_type not in EVENT_TYPES:
            raise GroupVisitError(400, "INVALID_ATTENDANCE_ACTION", "eventType must be CheckIn or CheckOut.")
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM group_visits WHERE group_event_id=?", (group_id,)).fetchone()
        if row is None:
            raise GroupVisitError(404, "GROUP_NOT_FOUND", "Group booking was not found.")
        if row["status"] == "Cancelled":
            raise GroupVisitError(409, "GROUP_CANCELLED", "Cancelled group bookings cannot check in or out.")
        if event_type == "CheckOut" and not row["arrival_at_utc"]:
            raise GroupVisitError(409, "GROUP_CHECKIN_REQUIRED", "The group must have a recorded arrival before group checkout.")
        if event_type == "CheckIn" and row["departure_at_utc"]:
            raise GroupVisitError(409, "GROUP_ALREADY_DEPARTED", "This group has already departed; reopen through the approved reconciliation path.")
        return row

    def create_scan(self, group_id: str, payload: object, *, mode: str) -> dict:
        if not isinstance(payload, dict):
            raise GroupVisitError(400, "INVALID_SCAN", "Request body must be a JSON object.")
        event_type = payload.get("eventType")
        row = self._validate_scan_state(group_id, event_type)
        image_data = payload.get("imageData")
        try:
            raw = _image_bytes(image_data)
        except FaceAttendanceError as exc:
            raise GroupVisitError(exc.status, exc.code, exc.message) from exc
        if len(raw) > MAX_GROUP_PHOTO_BYTES:
            raise GroupVisitError(413, "GROUP_PHOTO_TOO_LARGE", "Group image exceeds the local storage limit.")
        width = payload.get("imageWidth")
        height = payload.get("imageHeight")
        if isinstance(width, bool) or not isinstance(width, int) or not 160 <= width <= 1920 or isinstance(height, bool) or not isinstance(height, int) or not 120 <= height <= 1440:
            raise GroupVisitError(400, "IMAGE_DIMENSIONS_INVALID", "Camera width/height must be supplied within the supported image bounds.")
        retention = self.photo_retention_config()
        protected = None
        expires = None
        status = retention["status"]
        if status == "encrypted-retention-enabled":
            try:
                protected = self._protect(raw, f"Stamford group visit evidence {group_id} {event_type}")
                expires = _iso(_now() + timedelta(days=int(retention["retentionDays"])))
            except FaceAttendanceError as exc:
                status = "retention-error"
            except Exception:
                status = "retention-error"
        scan_id = "GSCAN-" + uuid.uuid4().hex.upper()
        now = _now()
        if mode not in {"mock", "live"}:
            raise GroupVisitError(400, "INVALID_GROUP_MODE", "Group visit mode must be mock or live.")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("INSERT INTO group_visit_scans(scan_id,group_event_id,event_type,captured_at_utc,expires_at_utc,image_sha256,image_width,image_height,mode,photo_protected,photo_retention_expires_at_utc,photo_retention_status,match_result_json,annotations_json,scan_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (scan_id, group_id, event_type, _iso(now), _iso(now + timedelta(seconds=180)), hashlib.sha256(raw).hexdigest(), width, height, mode, protected, expires, status, "{}", "[]", "Captured"))
                # A camera capture is the group arrival/departure evidence even if some
                # faces cannot be matched. Individual attendance is updated only after
                # confirmation or a separate human reconciliation.
                self._update_group_status(conn, group_id, event_type, _clean_text(payload.get("operator"), "operator", 100) or "Prototype operator")
                self._audit(conn, group_id, "GroupPhotoCaptured", _clean_text(payload.get("operator"), "operator", 100) or "Prototype operator", "Success", {"scanId": scan_id, "eventType": event_type, "photoRetained": bool(protected), "imageWidth": width, "imageHeight": height, "mode": mode})
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"scanId": scan_id, "groupEventId": group_id, "eventType": event_type, "capturedAtUtc": _iso(now), "imageSha256": hashlib.sha256(raw).hexdigest(), "photoRetentionStatus": status, "photoRetained": bool(protected), "photoRetentionReason": retention["reason"], "mode": mode}

    def mock_match_result(self, group_id: str, scan_meta: dict, image_width: int, image_height: int) -> dict:
        """Return visibly simulated suggestions for UI rehearsal; image pixels are not analysed."""
        booking = self.get_booking(group_id)
        members = booking["roster"][:min(len(booking["roster"]), 12)]
        cols = min(4, max(1, len(members)))
        rows = max(1, (len(members) + cols - 1) // cols)
        cell_w = max(1, image_width // cols)
        cell_h = max(1, image_height // rows)
        candidates = []
        for index, member in enumerate(members, start=1):
            col, row = (index - 1) % cols, (index - 1) // cols
            box_w = max(48, int(cell_w * 0.36))
            box_h = max(48, int(cell_h * 0.50))
            x = min(max(0, col * cell_w + (cell_w - box_w) // 2), max(0, image_width - box_w))
            y = min(max(0, row * cell_h + (cell_h - box_h) // 2), max(0, image_height - box_h))
            candidates.append({
                "faceIndex": index, "boundingBox": {"x": x, "y": y, "width": box_w, "height": box_h},
                "matched": True, "simulated": True, "memberId": member["memberId"],
                "candidate": {"studentId": member.get("faceProfileId") or member["memberId"], "personType": member["personType"], "displayName": member["displayName"], "className": member.get("affiliation") or "Group guest", "externalId": member.get("externalId") or "DEMO ONLY"},
                "ticketId": "GMOCK-" + secrets.token_urlsafe(18), "humanConfirmationRequired": True,
            })
        return {"ok": True, "mode": "mock", "simulated": True, "matchingAvailable": bool(candidates), "groupEventId": group_id, "scanId": scan_meta["scanId"], "eventType": scan_meta["eventType"], "detectedFaceCount": len(candidates), "imageWidth": image_width, "imageHeight": image_height, "candidates": candidates, "unmatchedRosterCount": max(0, len(booking["roster"]) - len(candidates)), "groupImageUsedForEnrollment": False, "humanRosterReconciliationRequired": True, "message": "SIMULATED DEMO ONLY: the webcam photo was not analysed for identity. These illustrative boxes/names are scripted so you can rehearse group attendance."}

    def update_scan_match(self, scan_id: str, match_result: dict) -> None:
        safe = dict(match_result)
        safe.pop("imageData", None)
        safe.pop("rawImage", None)
        with closing(self._connect()) as conn:
            conn.execute("UPDATE group_visit_scans SET match_result_json=? WHERE scan_id=?", (json.dumps(safe, ensure_ascii=False, separators=(",", ":")), scan_id))

    def get_scan(self, group_id: str, scan_id: str) -> dict:
        group_id = self._validate_group_id(group_id)
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM group_visit_scans WHERE scan_id=? AND group_event_id=?", (scan_id, group_id)).fetchone()
        if row is None:
            raise GroupVisitError(404, "GROUP_SCAN_NOT_FOUND", "Group scan/photo evidence record was not found for this booking.")
        if datetime.fromisoformat(row["expires_at_utc"].replace("Z", "+00:00")) <= _now() and row["scan_status"].casefold() != "confirmed":
            raise GroupVisitError(409, "GROUP_SCAN_EXPIRED", "Group scan review expired. Capture a fresh group photo and review matches again.")
        try:
            match = json.loads(row["match_result_json"] or "{}")
            annotations = json.loads(row["annotations_json"] or "[]")
        except json.JSONDecodeError:
            match, annotations = {}, []
        return {"scanId": row["scan_id"], "groupEventId": row["group_event_id"], "eventType": row["event_type"], "capturedAtUtc": row["captured_at_utc"], "imageSha256": row["image_sha256"], "imageWidth": row["image_width"], "imageHeight": row["image_height"], "mode": row["mode"], "photoRetained": bool(row["photo_protected"]), "photoRetentionStatus": row["photo_retention_status"], "matchResult": match, "annotations": annotations, "scanStatus": row["scan_status"]}

    def _audit(self, conn: sqlite3.Connection, group_id: str, action: str, actor: str, outcome: str, details: dict | None = None):
        conn.execute("INSERT INTO group_visit_audit(group_event_id,occurred_at_utc,action,actor,outcome,detail_json) VALUES(?,?,?,?,?,?)", (group_id, _iso(), action, actor[:100], outcome[:80], json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))))

    def _validate_scan_confirmations(self, group_id: str, scan_id: str, confirmations: object, operator: object, event_type: str) -> tuple[dict, list[dict], str]:
        if not isinstance(confirmations, list) or not confirmations or len(confirmations) > 40:
            raise GroupVisitError(400, "INVALID_CONFIRMATION_BATCH", "Select between 1 and 40 reviewed roster matches.")
        actor = _clean_text(operator, "operator", 100, required=True)
        scan = self.get_scan(group_id, scan_id)
        if scan.get("scanStatus") != "Captured":
            raise GroupVisitError(409, "GROUP_SCAN_ALREADY_FINALISED", "This scan has already been confirmed or reconciled.")
        if scan["eventType"] != event_type:
            raise GroupVisitError(409, "GROUP_SCAN_ACTION_MISMATCH", "The selected group scan belongs to a different attendance action.")
        candidates = scan["matchResult"].get("candidates") if isinstance(scan["matchResult"], dict) else []
        by_ticket = {x.get("ticketId"): x for x in candidates if isinstance(x, dict) and x.get("ticketId")}
        seen_member_ids = set()
        seen_tickets = set()
        clean = []
        booking = self.get_booking(group_id)
        member_by_profile = {m["faceProfileId"]: m for m in booking["roster"] if m.get("faceProfileId")}
        member_by_id = {m["memberId"]: m for m in booking["roster"]}
        for item in confirmations:
            if not isinstance(item, dict) or item.get("confirmMatch") is not True:
                raise GroupVisitError(400, "HUMAN_CONFIRMATION_REQUIRED", "Every attendee needs explicit confirmation after reviewing the group-photo annotation.")
            ticket = _clean_text(item.get("ticketId"), "ticketId", 100, required=True)
            member_id = _clean_text(item.get("memberId"), "memberId", 64, required=True)
            if ticket in seen_tickets or member_id in seen_member_ids:
                raise GroupVisitError(400, "DUPLICATE_BATCH_ITEM", "A person or match ticket may be selected only once per batch.")
            seen_tickets.add(ticket)
            seen_member_ids.add(member_id)
            candidate = by_ticket.get(ticket)
            member = member_by_id.get(member_id)
            if not candidate or not member:
                raise GroupVisitError(409, "GROUP_MATCH_NOT_IN_SCAN", "A selected person/ticket was not part of this group's latest scan result.")
            if scan["mode"] == "live":
                profile_id = item.get("studentId")
                if not member.get("faceProfileId") or profile_id != member.get("faceProfileId") or candidate.get("candidate", {}).get("studentId") != profile_id:
                    raise GroupVisitError(409, "GROUP_PROFILE_MISMATCH", "The selected live face match does not resolve to the selected approved group-roster profile.")
            elif candidate.get("memberId") != member_id:
                raise GroupVisitError(409, "GROUP_MEMBER_MISMATCH", "The selected simulated match does not belong to the selected roster member.")
            with closing(self._connect()) as conn:
                duplicate = conn.execute("SELECT 1 FROM group_visit_attendance WHERE member_id=? AND group_event_id=? AND event_type=?", (member_id, group_id, event_type)).fetchone()
                prior_checkin = conn.execute("SELECT 1 FROM group_visit_attendance WHERE member_id=? AND group_event_id=? AND event_type='CheckIn'", (member_id, group_id)).fetchone()
            if duplicate:
                raise GroupVisitError(409, "ATTENDANCE_ALREADY_RECORDED", f"{member['displayName']} already has a {event_type} event for this group.")
            if event_type == "CheckOut" and prior_checkin is None:
                raise GroupVisitError(409, "CHECKIN_REQUIRED", f"{member['displayName']} cannot check out before their group check-in is recorded.")
            clean.append({"ticketId": ticket, "memberId": member_id, "studentId": member.get("faceProfileId"), "confirmMatch": True, "faceIndex": candidate.get("faceIndex"), "candidate": candidate.get("candidate", {}), "boundingBox": candidate.get("boundingBox")})
        return scan, clean, actor

    def validate_live_confirmations(self, group_id: str, scan_id: str, event_type: str, confirmations: object, operator: object) -> dict:
        scan, clean, actor = self._validate_scan_confirmations(group_id, scan_id, confirmations, operator, event_type)
        if scan["mode"] != "live":
            raise GroupVisitError(409, "SCAN_MODE_MISMATCH", "This is a simulated scan, not a live face match.")
        face_items = [{"ticketId": x["ticketId"], "studentId": x["studentId"], "confirmMatch": True} for x in clean]
        return {"scan": scan, "confirmations": clean, "faceConfirmations": face_items, "operator": actor}

    def _record_roster_events(self, conn: sqlite3.Connection, group_id: str, scan_id: str, event_type: str, clean: list[dict], actor: str, mode: str, gen_sync_by_profile: dict | None = None) -> list[dict]:
        now = _iso()
        gen_sync_by_profile = gen_sync_by_profile or {}
        annotations = []
        events = []
        for item in clean:
            member_id = item["memberId"]
            row = conn.execute("SELECT * FROM group_visit_roster WHERE member_id=? AND group_event_id=?", (member_id, group_id)).fetchone()
            if row is None:
                raise GroupVisitError(404, "GROUP_MEMBER_NOT_FOUND", "The roster member was removed or does not belong to this group.")
            duplicate = conn.execute("SELECT attendance_id FROM group_visit_attendance WHERE member_id=? AND group_event_id=? AND event_type=?", (member_id, group_id, event_type)).fetchone()
            if duplicate:
                raise GroupVisitError(409, "ATTENDANCE_ALREADY_RECORDED", f"{row['display_name']} already has a {event_type} event for this group.")
            if event_type == "CheckOut":
                checked_in = conn.execute("SELECT attendance_id FROM group_visit_attendance WHERE member_id=? AND group_event_id=? AND event_type='CheckIn'", (member_id, group_id)).fetchone()
                if checked_in is None:
                    raise GroupVisitError(409, "CHECKIN_REQUIRED", f"{row['display_name']} cannot check out before their group check-in is recorded.")
            attendance_id = "GATT-" + secrets.token_hex(10).upper()
            sync_status = gen_sync_by_profile.get(row["face_profile_id"], "not-linked")
            conn.execute("INSERT INTO group_visit_attendance(attendance_id,group_event_id,member_id,event_type,occurred_at_utc,operator_name,confirmation_method,match_ticket_id,genetec_sync_status) VALUES(?,?,?,?,?,?,?,?,?)", (attendance_id, group_id, member_id, event_type, now, actor, "GroupFaceMatch" if mode == "live" else "MockGroupMatch", item["ticketId"], sync_status))
            if event_type == "CheckIn":
                conn.execute("UPDATE group_visit_roster SET attendance_state='CheckedIn',checked_in_at_utc=?,last_review_note='' WHERE member_id=?", (now, member_id))
            else:
                conn.execute("UPDATE group_visit_roster SET attendance_state='CheckedOut',checked_out_at_utc=?,last_review_note='' WHERE member_id=?", (now, member_id))
            details = {"faceIndex": item.get("faceIndex"), "memberId": member_id, "displayName": row["display_name"], "personType": row["person_type"], "candidateProfileId": item.get("studentId"), "confirmationMethod": "HumanConfirmed" if mode == "live" else "SimulatedCandidateConfirmed", "simulated": mode != "live", "boundingBox": item.get("boundingBox")}
            annotations.append(details)
            events.append({"attendanceId": attendance_id, "memberId": member_id, "displayName": row["display_name"], "personType": row["person_type"], "eventType": event_type, "occurredAtUtc": now, "genetecSyncStatus": sync_status, "simulated": mode != "live"})
        return events, annotations

    def confirm_mock_scan(self, group_id: str, payload: object) -> dict:
        if not isinstance(payload, dict) or payload.get("confirmBatch") is not True:
            raise GroupVisitError(400, "HUMAN_CONFIRMATION_REQUIRED", "Confirm the reviewed simulated roster selections before proceeding.")
        scan_id = _clean_text(payload.get("scanId"), "scanId", 80, required=True)
        event_type = payload.get("eventType")
        if event_type not in EVENT_TYPES:
            raise GroupVisitError(400, "INVALID_ATTENDANCE_ACTION", "eventType must be CheckIn or CheckOut.")
        scan, clean, actor = self._validate_scan_confirmations(group_id, scan_id, payload.get("confirmations"), payload.get("operator"), event_type)
        if scan["mode"] != "mock":
            raise GroupVisitError(409, "SCAN_MODE_MISMATCH", "This is not a simulated group scan.")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current_scan = conn.execute("SELECT scan_status FROM group_visit_scans WHERE scan_id=?", (scan_id,)).fetchone()
                if not current_scan or current_scan["scan_status"] != "Captured":
                    raise GroupVisitError(409, "GROUP_SCAN_ALREADY_FINALISED", "This scan has already been confirmed or reconciled.")
                events, annotations = self._record_roster_events(conn, group_id, scan_id, event_type, clean, actor, "mock")
                conn.execute("UPDATE group_visit_scans SET annotations_json=?,scan_status='Confirmed' WHERE scan_id=?", (json.dumps(annotations, ensure_ascii=False, separators=(",", ":")), scan_id))
                self._update_group_status(conn, group_id, event_type, actor)
                self._audit(conn, group_id, "GroupAttendanceBatchRecorded", actor, "Simulated", {"scanId": scan_id, "eventType": event_type, "recordedCount": len(events), "simulated": True})
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"ok": True, "groupEventId": group_id, "scanId": scan_id, "eventType": event_type, "recordedCount": len(events), "events": events, "annotations": annotations, "simulated": True, "writtenToGenetec": False, "photoRetained": scan["photoRetained"], "groupImageUsedForEnrollment": False, "humanConfirmed": True}

    def finalize_live_scan(self, group_id: str, payload: dict, live_result: dict) -> dict:
        scan_id = payload["scanId"]
        event_type = payload["eventType"]
        scan, clean, actor = self._validate_scan_confirmations(group_id, scan_id, payload.get("confirmations"), payload.get("operator"), event_type)
        if scan["mode"] != "live":
            raise GroupVisitError(409, "SCAN_MODE_MISMATCH", "This is not a live group scan.")
        selected_profiles = {x["studentId"] for x in clean}
        sync_by_profile = {}
        for ev in live_result.get("events", []):
            profile_id = ev.get("studentId")
            if profile_id in selected_profiles:
                sync_by_profile[profile_id] = ev.get("genetecSyncStatus", "not-linked")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                scan_row = conn.execute("SELECT scan_status FROM group_visit_scans WHERE scan_id=?", (scan_id,)).fetchone()
                if not scan_row or scan_row["scan_status"] != "Captured":
                    raise GroupVisitError(409, "GROUP_SCAN_ALREADY_FINALISED", "This scan has already been confirmed or reconciled.")
                events, annotations = self._record_roster_events(conn, group_id, scan_id, event_type, clean, actor, "live", sync_by_profile)
                conn.execute("UPDATE group_visit_scans SET annotations_json=?,scan_status='Confirmed' WHERE scan_id=?", (json.dumps(annotations, ensure_ascii=False, separators=(",", ":")), scan_id))
                self._update_group_status(conn, group_id, event_type, actor)
                self._audit(conn, group_id, "GroupAttendanceBatchRecorded", actor, "Success", {"scanId": scan_id, "eventType": event_type, "recordedCount": len(events), "simulated": False})
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"ok": True, "groupEventId": group_id, "scanId": scan_id, "eventType": event_type, "recordedCount": len(events), "events": events, "annotations": annotations, "simulated": False, "writtenToGenetec": any(x.get("genetecSyncStatus") == "raised-unverified" for x in events), "photoRetained": scan["photoRetained"], "groupImageUsedForEnrollment": False, "humanConfirmed": True}

    def _update_group_status(self, conn: sqlite3.Connection, group_id: str, event_type: str, actor: str) -> None:
        now = _iso()
        if event_type == "CheckIn":
            conn.execute("UPDATE group_visits SET status='Arrived — reconciliation required',arrival_at_utc=COALESCE(arrival_at_utc,?),updated_at_utc=?,reconciled_at_utc=NULL WHERE group_event_id=?", (now, now, group_id))
        else:
            conn.execute("UPDATE group_visits SET status='Departed — reconciliation required',departure_at_utc=COALESCE(departure_at_utc,?),updated_at_utc=?,reconciled_at_utc=NULL WHERE group_event_id=?", (now, now, group_id))

    def reconcile(self, group_id: str, payload: object) -> dict:
        group_id = self._validate_group_id(group_id)
        if not isinstance(payload, dict):
            raise GroupVisitError(400, "INVALID_RECONCILIATION", "Request body must be a JSON object.")
        actor = _clean_text(payload.get("operator"), "operator", 100, required=True)
        note = _clean_text(payload.get("reason"), "reason", 500, required=True)
        decisions = payload.get("decisions")
        if not isinstance(decisions, list) or not decisions or len(decisions) > MAX_ROSTER:
            raise GroupVisitError(400, "INVALID_RECONCILIATION", f"decisions must contain 1–{MAX_ROSTER} roster decisions.")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                group = conn.execute("SELECT * FROM group_visits WHERE group_event_id=?", (group_id,)).fetchone()
                if not group:
                    raise GroupVisitError(404, "GROUP_NOT_FOUND", "Group booking was not found.")
                if not group["arrival_at_utc"]:
                    raise GroupVisitError(409, "GROUP_ARRIVAL_REQUIRED", "The group must arrive before roster reconciliation.")
                post_departure = bool(group["departure_at_utc"])
                expected_member_ids = {r[0] for r in conn.execute("SELECT member_id FROM group_visit_roster WHERE group_event_id=?", (group_id,)).fetchall()}
                submitted_member_ids = {item.get("memberId") for item in decisions if isinstance(item, dict)}
                if submitted_member_ids != expected_member_ids or len(decisions) != len(expected_member_ids):
                    raise GroupVisitError(400, "ROSTER_RECONCILIATION_INCOMPLETE", "Submit exactly one explicit disposition for every member on the group roster.")
                seen = set()
                for item in decisions:
                    if not isinstance(item, dict):
                        raise GroupVisitError(400, "INVALID_RECONCILIATION", "Each decision must contain memberId and disposition.")
                    member_id = _clean_text(item.get("memberId"), "memberId", 64, required=True)
                    disposition = item.get("disposition")
                    if member_id in seen or disposition not in {"CheckedIn", "CheckedOut", "Absent", "Unresolved"}:
                        raise GroupVisitError(400, "INVALID_RECONCILIATION", "Duplicate member or invalid disposition in roster reconciliation.")
                    seen.add(member_id)
                    member = conn.execute("SELECT * FROM group_visit_roster WHERE member_id=? AND group_event_id=?", (member_id, group_id)).fetchone()
                    if not member:
                        raise GroupVisitError(404, "GROUP_MEMBER_NOT_FOUND", "Roster member does not belong to this group.")
                    if post_departure and disposition == "CheckedIn":
                        raise GroupVisitError(409, "DEPARTURE_STATUS_REQUIRED", "After departure, roster members must be checked out, confirmed absent or left unresolved.")
                    if not post_departure and disposition == "CheckedOut":
                        raise GroupVisitError(409, "GROUP_DEPARTURE_REQUIRED", "A roster member cannot be checked out before group departure.")
                    if disposition == "Absent" and (member["checked_in_at_utc"] or member["checked_out_at_utc"]):
                        raise GroupVisitError(409, "ATTENDANCE_ALREADY_RECORDED", f"{member['display_name']} already has a recorded attendance event and cannot be marked absent.")
                    if disposition == "CheckedIn" and member["checked_out_at_utc"]:
                        raise GroupVisitError(409, "ATTENDANCE_STATE_CONFLICT", f"{member['display_name']} is already checked out.")
                    if disposition == "CheckedIn" and not member["checked_in_at_utc"]:
                        now = _iso()
                        conn.execute("UPDATE group_visit_roster SET attendance_state='CheckedIn',checked_in_at_utc=?,last_review_note=? WHERE member_id=?", (now, note, member_id))
                        self._insert_manual_event(conn, group_id, member_id, "CheckIn", actor, note)
                    elif disposition == "CheckedOut" and not member["checked_out_at_utc"]:
                        if not member["checked_in_at_utc"]:
                            raise GroupVisitError(409, "CHECKIN_REQUIRED", f"{member['display_name']} cannot be checked out before being reconciled as checked in.")
                        now = _iso()
                        conn.execute("UPDATE group_visit_roster SET attendance_state='CheckedOut',checked_out_at_utc=?,last_review_note=? WHERE member_id=?", (now, note, member_id))
                        self._insert_manual_event(conn, group_id, member_id, "CheckOut", actor, note)
                    elif disposition == "Absent":
                        conn.execute("UPDATE group_visit_roster SET attendance_state='Absent',last_review_note=? WHERE member_id=?", (note, member_id))
                    elif disposition == "Unresolved":
                        conn.execute("UPDATE group_visit_roster SET attendance_state='Unresolved',last_review_note=? WHERE member_id=?", (note, member_id))
                roster = conn.execute("SELECT attendance_state FROM group_visit_roster WHERE group_event_id=?", (group_id,)).fetchall()
                fully_resolved = all(r["attendance_state"] in ({"CheckedOut", "Absent"} if post_departure else {"CheckedIn", "Absent"}) for r in roster)
                new_status = ("Reconciled" if fully_resolved and post_departure else "Arrival reconciled" if fully_resolved else "Exception review required" if any(r["attendance_state"] == "Unresolved" for r in roster) else "Roster pending")
                now = _iso()
                conn.execute("UPDATE group_visits SET status=?,reconciled_at_utc=?,reconciliation_note=?,updated_at_utc=? WHERE group_event_id=?", (new_status, now if fully_resolved else None, note, now, group_id))
                self._audit(conn, group_id, "GroupRosterReconciled", actor, new_status, {"decisionCount": len(decisions), "allMembersResolved": fully_resolved, "afterDeparture": post_departure, "reason": note})
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_booking(group_id)

    def _insert_manual_event(self, conn: sqlite3.Connection, group_id: str, member_id: str, event_type: str, actor: str, note: str) -> None:
        if conn.execute("SELECT 1 FROM group_visit_attendance WHERE member_id=? AND group_event_id=? AND event_type=?", (member_id, group_id, event_type)).fetchone():
            return
        conn.execute("INSERT INTO group_visit_attendance(attendance_id,group_event_id,member_id,event_type,occurred_at_utc,operator_name,confirmation_method,match_ticket_id,genetec_sync_status) VALUES(?,?,?,?,?,?,? ,NULL,'not-linked')", ("GATT-" + secrets.token_hex(10).upper(), group_id, member_id, event_type, _iso(), actor, "ManualReconciliation"))

    def purge_expired_photos(self) -> dict:
        now = _iso()
        with closing(self._connect()) as conn:
            cursor = conn.execute("UPDATE group_visit_scans SET photo_protected=NULL,photo_retention_status='retention-expired' WHERE photo_protected IS NOT NULL AND photo_retention_expires_at_utc IS NOT NULL AND photo_retention_expires_at_utc<=?", (now,))
            return {"ok": True, "photosPurged": cursor.rowcount}

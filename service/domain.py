"""Transactional prototype domain API for secure visitor passes.

This module is still prototype-only: the HTTP listener is loopback-bound and
has no enterprise identity or role-based authorization. Use synthetic records.
Raw QR tokens are returned only at issuance/rotation and are never persisted;
SQLite keeps SHA-256 hashes for server-side validation/revocation.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import re
import secrets
import sqlite3
import unicodedata
from pathlib import Path

# Stamford American International School operates in Singapore. Visit dates and
# clock times are school-local values, not the Windows host/browser timezone.
# Singapore uses UTC+08:00 year-round. Keep all visit policy comparisons anchored
# to this zone so client/server timezone settings cannot reject a valid pass.
SCHOOL_TIMEZONE = timezone(timedelta(hours=8), "Asia/Singapore")
SCHOOL_TIMEZONE_NAME = "Asia/Singapore"

try:
    from .qr_code import make_svg
except ImportError:  # direct script execution fallback
    from qr_code import make_svg

BOOKING_RE = re.compile(r"^VMS-[A-Z0-9][A-Z0-9-]{2,79}$", re.I)
WINDOW_RE = re.compile(r"^\s*([0-9]{1,2}:[0-9]{2})\s*(?:-|to)\s*([0-9]{1,2}:[0-9]{2})\s*$", re.I)
WINDOW_DASHES = str.maketrans({ch: "-" for ch in "‐‑‒–—―−﹘﹣－"})
WINDOW_IGNORABLE = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
WINDOW_UNICODE_SPACE = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
VISITOR_TYPE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
VISITOR_TYPE_SEEDS = (
    ("parent-guardian", "Parent / Guardian", "Parent, guardian or authorised family contact", 10),
    ("prospective-family", "Prospective Family", "Prospective student or family visit", 20),
    ("contractor", "Contractor", "Approved contractor working on campus", 30),
    ("vendor-supplier", "Vendor / Supplier", "Supplier or business representative", 40),
    ("delivery-courier", "Delivery / Courier", "Parcel, food or goods delivery", 50),
    ("service-provider", "Service Provider", "Maintenance, cleaning or other service visit", 60),
    ("event-guest", "Event Guest", "Guest attending a school event", 70),
    ("volunteer", "Volunteer", "Approved school volunteer", 80),
    ("alumni", "Alumni", "Former student or alumni visit", 90),
    ("staff-employee", "Staff / Employee", "Staff member attending as a visitor", 100),
    ("student", "Student", "Student visit recorded through the visitor invitation workflow", 110),
    ("general-visitor", "General Visitor", "General visitor when no more specific type applies", 120),
    ("other", "Other", "Other visitor category", 130),
)


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    value = value or utc_now()
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonicalize_window(raw: str) -> str:
    """Return a stable HH:MM–HH:MM representation despite Unicode dash/space variants."""
    if not isinstance(raw, str):
        raise DomainError(400, "INVALID_VISIT_WINDOW", "Visit window must be a start and end time, for example 09:00–10:00.")
    normalized = unicodedata.normalize("NFKC", raw)
    normalized = WINDOW_IGNORABLE.sub("", normalized)
    normalized = WINDOW_UNICODE_SPACE.sub(" ", normalized)
    normalized = normalized.translate(WINDOW_DASHES).strip()
    match = WINDOW_RE.fullmatch(normalized)
    if not match:
        raise DomainError(400, "INVALID_VISIT_WINDOW", "Visit window must use a start and end clock time, for example 09:00–10:00.")
    try:
        sh, sm = (int(part) for part in match.group(1).split(":"))
        eh, em = (int(part) for part in match.group(2).split(":"))
        start = time(sh, sm)
        end = time(eh, em)
    except (ValueError, TypeError) as exc:
        raise DomainError(400, "INVALID_VISIT_WINDOW", "Visit window contains an invalid clock time; use hours 00–23 and minutes 00–59.") from exc
    if end <= start:
        raise DomainError(400, "INVALID_VISIT_WINDOW", "Visit window end must be after its start and within the same day.")
    return f"{start.hour:02d}:{start.minute:02d}–{end.hour:02d}:{end.minute:02d}"


def parse_window(raw: str) -> tuple[time, time]:
    canonical = canonicalize_window(raw)
    match = WINDOW_RE.fullmatch(canonical.replace("–", "-"))
    assert match is not None  # canonicalize_window has already validated this value
    sh, sm = (int(part) for part in match.group(1).split(":"))
    eh, em = (int(part) for part in match.group(2).split(":"))
    return time(sh, sm), time(eh, em)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def utc_string_to_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class DomainStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

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
                CREATE TABLE IF NOT EXISTS domain_visits (
                    booking_id TEXT PRIMARY KEY,
                    visitor_name TEXT NOT NULL,
                    visitor_email TEXT NOT NULL DEFAULT '',
                    host_name TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    visit_date TEXT NOT NULL,
                    visit_window TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('Invited','Inside','Overdue','Checked out','Cancelled')),
                    qr_token_hash TEXT UNIQUE,
                    qr_expires_at_utc TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    checked_in_at_utc TEXT,
                    checked_out_at_utc TEXT,
                    revoked_at_utc TEXT,
                    row_version INTEGER NOT NULL DEFAULT 1,
                    visitor_type_id TEXT NOT NULL DEFAULT 'general-visitor',
                    visitor_type_name TEXT NOT NULL DEFAULT 'General Visitor'
                );
                CREATE TABLE IF NOT EXISTS domain_events (
                    event_id TEXT PRIMARY KEY,
                    booking_id TEXT NOT NULL REFERENCES domain_visits(booking_id),
                    action TEXT NOT NULL CHECK(action IN ('CheckIn','CheckOut')),
                    occurred_at_utc TEXT NOT NULL,
                    prior_status TEXT NOT NULL,
                    resulting_status TEXT NOT NULL,
                    qr_token_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_domain_visits_status ON domain_visits(status);
                CREATE INDEX IF NOT EXISTS idx_domain_visits_qr_expiry ON domain_visits(qr_expires_at_utc);
                CREATE TABLE IF NOT EXISTS domain_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at_utc TEXT NOT NULL,
                    action TEXT NOT NULL,
                    booking_id TEXT,
                    outcome TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS domain_visitor_types (
                    visitor_type_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
                    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK(is_builtin IN (0,1)),
                    sort_order INTEGER NOT NULL DEFAULT 1000,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );
            """)
            # Upgrade databases created before visitor types were introduced.
            # Keep current visit state and QR hashes unchanged during migration.
            visit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(domain_visits)").fetchall()}
            if "visitor_type_id" not in visit_columns:
                conn.execute("ALTER TABLE domain_visits ADD COLUMN visitor_type_id TEXT NOT NULL DEFAULT 'general-visitor'")
            if "visitor_type_name" not in visit_columns:
                conn.execute("ALTER TABLE domain_visits ADD COLUMN visitor_type_name TEXT NOT NULL DEFAULT 'General Visitor'")
            stamp = iso_utc()
            for type_id, name, description, sort_order in VISITOR_TYPE_SEEDS:
                conn.execute(
                    """INSERT OR IGNORE INTO domain_visitor_types(
                        visitor_type_id,display_name,description,is_active,is_builtin,sort_order,created_at_utc,updated_at_utc)
                        VALUES(?,?,?,?,?,?,?,?)""",
                    (type_id, name, description, 1, 1, sort_order, stamp, stamp),
                )

    def _audit(self, conn: sqlite3.Connection, action: str, booking_id: str | None, outcome: str, details: dict | None = None) -> None:
        conn.execute(
            "INSERT INTO domain_audit(occurred_at_utc,action,booking_id,outcome,detail_json) VALUES(?,?,?,?,?)",
            (iso_utc(), action, booking_id, outcome, json.dumps(details or {}, separators=(",", ":"))),
        )

    def _expires_for(self, visit_date: str, visit_window: str) -> datetime:
        _, end_time = parse_window(visit_window)
        visit_day = date.fromisoformat(visit_date)
        local_end = datetime.combine(visit_day, end_time, tzinfo=SCHOOL_TIMEZONE)
        now = utc_now()
        # Visit date/times are always interpreted in school time, independently of
        # the Windows host timezone. Token expiry is UTC; visit-window policy is separate.
        expiry = local_end.astimezone(timezone.utc) + timedelta(hours=6)
        return max(expiry, now + timedelta(hours=1))

    def _public(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["booking_id"],
            "name": row["visitor_name"],
            "email": row["visitor_email"],
            "host": row["host_name"],
            "destination": row["destination"],
            "purpose": row["purpose"],
            "visitorTypeId": row["visitor_type_id"],
            "visitorType": row["visitor_type_name"],
            "visitDate": row["visit_date"],
            "window": row["visit_window"],
            "status": row["status"],
            "createdAtUtc": row["created_at_utc"],
            "updatedAtUtc": row["updated_at_utc"],
            "arrivalUtc": row["checked_in_at_utc"],
            "departureUtc": row["checked_out_at_utc"],
            "qrExpiresAtUtc": row["qr_expires_at_utc"],
            "securePass": True,
            "rowVersion": row["row_version"],
        }

    @staticmethod
    def _clean_type_text(value: object, field: str, maximum: int, *, required: bool) -> str:
        if value is None and not required:
            return ""
        if not isinstance(value, str):
            raise DomainError(400, "INVALID_VISITOR_TYPE_FIELD", f"Field '{field}' must be text.")
        normalized = unicodedata.normalize("NFKC", value)
        if any(unicodedata.category(ch).startswith("C") for ch in normalized):
            raise DomainError(400, "INVALID_VISITOR_TYPE_FIELD", f"Field '{field}' contains unsupported control characters.")
        cleaned = " ".join(normalized.split())
        if required and not cleaned:
            raise DomainError(400, "INVALID_VISITOR_TYPE_FIELD", f"Field '{field}' is required.")
        if len(cleaned) > maximum:
            raise DomainError(400, "INVALID_VISITOR_TYPE_FIELD", f"Field '{field}' must be at most {maximum} characters.")
        if required and len(cleaned) < 2:
            raise DomainError(400, "INVALID_VISITOR_TYPE_FIELD", f"Field '{field}' must contain at least 2 characters.")
        return cleaned

    @staticmethod
    def _public_visitor_type(row: sqlite3.Row) -> dict:
        return {
            "id": row["visitor_type_id"],
            "name": row["display_name"],
            "description": row["description"],
            "isActive": bool(row["is_active"]),
            "isBuiltIn": bool(row["is_builtin"]),
            "createdAtUtc": row["created_at_utc"],
            "updatedAtUtc": row["updated_at_utc"],
        }

    def list_visitor_types(self, include_inactive: bool = True) -> dict:
        where = "" if include_inactive else "WHERE is_active=1"
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM domain_visitor_types {where} ORDER BY sort_order, display_name COLLATE NOCASE"
            ).fetchall()
        return {
            "ok": True,
            "visitorTypes": [self._public_visitor_type(row) for row in rows],
            "source": "transactional-local-domain-store",
            "persistence": "sqlite-local",
        }

    def create_visitor_type(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise DomainError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        name = self._clean_type_text(payload.get("name"), "name", 64, required=True)
        description = self._clean_type_text(payload.get("description", ""), "description", 160, required=False)
        stamp = iso_utc()
        type_id = "custom-" + secrets.token_hex(8)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT INTO domain_visitor_types(
                        visitor_type_id,display_name,description,is_active,is_builtin,sort_order,created_at_utc,updated_at_utc)
                        VALUES(?,?,?,?,?,?,?,?)""",
                    (type_id, name, description, 1, 0, 1000, stamp, stamp),
                )
                self._audit(conn, "VisitorTypeCreated", None, "Success", {"visitorTypeId": type_id, "name": name})
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise DomainError(409, "VISITOR_TYPE_NAME_EXISTS", "A visitor type with that name already exists.") from exc
        return {"ok": True, "visitorType": self.get_visitor_type(type_id)}

    def get_visitor_type(self, type_id: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM domain_visitor_types WHERE visitor_type_id=?", (type_id,)).fetchone()
        return self._public_visitor_type(row) if row else None

    def update_visitor_type(self, type_id: str, payload: object) -> dict:
        if not VISITOR_TYPE_ID_RE.fullmatch(type_id):
            raise DomainError(400, "INVALID_VISITOR_TYPE_ID", "Visitor type ID is invalid.")
        if not isinstance(payload, dict):
            raise DomainError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        if not any(key in payload for key in ("name", "description", "isActive")):
            raise DomainError(400, "EMPTY_VISITOR_TYPE_UPDATE", "Provide a name, description or isActive value to update.")
        stamp = iso_utc()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM domain_visitor_types WHERE visitor_type_id=?", (type_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise DomainError(404, "VISITOR_TYPE_NOT_FOUND", "That visitor type does not exist.")
            name = self._clean_type_text(payload.get("name", row["display_name"]), "name", 64, required=True)
            description = self._clean_type_text(payload.get("description", row["description"]), "description", 160, required=False)
            is_active = row["is_active"]
            if "isActive" in payload:
                if type(payload["isActive"]) is not bool:
                    conn.execute("ROLLBACK")
                    raise DomainError(400, "INVALID_VISITOR_TYPE_STATUS", "isActive must be true or false.")
                is_active = int(payload["isActive"])
            if row["is_active"] and not is_active:
                active_count = conn.execute("SELECT COUNT(*) FROM domain_visitor_types WHERE is_active=1").fetchone()[0]
                if active_count <= 1:
                    conn.execute("ROLLBACK")
                    raise DomainError(409, "LAST_ACTIVE_VISITOR_TYPE", "At least one visitor type must remain active.")
            try:
                conn.execute(
                    "UPDATE domain_visitor_types SET display_name=?,description=?,is_active=?,updated_at_utc=? WHERE visitor_type_id=?",
                    (name, description, is_active, stamp, type_id),
                )
                self._audit(conn, "VisitorTypeUpdated", None, "Success", {"visitorTypeId": type_id, "isActive": bool(is_active), "name": name})
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise DomainError(409, "VISITOR_TYPE_NAME_EXISTS", "A visitor type with that name already exists.") from exc
        return {"ok": True, "visitorType": self.get_visitor_type(type_id)}

    def list_visits(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM domain_visits ORDER BY created_at_utc DESC").fetchall()
        return [self._public(row) for row in rows]

    def get_visit(self, booking_id: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM domain_visits WHERE booking_id=?", (booking_id,)).fetchone()
        return self._public(row) if row else None

    def create_visit(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise DomainError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        booking_id = payload.get("id")
        name = payload.get("name")
        host = payload.get("host")
        destination = payload.get("destination")
        visit_date = payload.get("visitDate")
        visit_window = payload.get("window")
        email = payload.get("email", "")
        purpose = payload.get("purpose", payload.get("note", ""))
        visitor_type_id = payload.get("visitorTypeId", "general-visitor")
        for field, value, maximum in (("id", booking_id, 83), ("name", name, 90), ("host", host, 90), ("destination", destination, 120), ("visitDate", visit_date, 10), ("window", visit_window, 32), ("email", email, 120), ("purpose", purpose, 200)):
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
                if field in {"email", "purpose"} and (value is None or value == ""):
                    continue
                raise DomainError(400, "INVALID_FIELD", f"Field '{field}' is required and must be at most {maximum} characters.")
        booking_id = booking_id.strip().upper()
        if not BOOKING_RE.fullmatch(booking_id):
            raise DomainError(400, "INVALID_BOOKING_ID", "Booking ID must start with VMS- and contain only letters, digits and hyphens.")
        if not isinstance(visitor_type_id, str) or not VISITOR_TYPE_ID_RE.fullmatch(visitor_type_id.strip().lower()):
            raise DomainError(400, "INVALID_VISITOR_TYPE_ID", "Select a valid visitor type from the active visitor-type list.")
        visitor_type_id = visitor_type_id.strip().lower()
        try:
            parsed_date = date.fromisoformat(visit_date)
        except ValueError as exc:
            raise DomainError(400, "INVALID_VISIT_DATE", "Visit date must use YYYY-MM-DD format.") from exc
        visit_window = canonicalize_window(visit_window)
        now = utc_now()
        local_today = now.astimezone(SCHOOL_TIMEZONE).date()
        if parsed_date < local_today:
            raise DomainError(400, "VISIT_DATE_IN_PAST", "New invitations cannot be created for a past local date.")
        if parsed_date > local_today + timedelta(days=365):
            raise DomainError(400, "VISIT_DATE_TOO_FAR", "Invitations can be created no more than 365 days in advance; issue/rotate the pass closer to the visit date.")
        token = secrets.token_urlsafe(32)
        expires = self._expires_for(visit_date, visit_window)
        stamp = iso_utc(now)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                visitor_type = conn.execute(
                    "SELECT display_name FROM domain_visitor_types WHERE visitor_type_id=? AND is_active=1",
                    (visitor_type_id,),
                ).fetchone()
                if visitor_type is None:
                    conn.execute("ROLLBACK")
                    raise DomainError(400, "VISITOR_TYPE_INACTIVE_OR_UNKNOWN", "The selected visitor type is unknown or inactive. Refresh the visitor-type list and choose an active type.")
                visitor_type_name = visitor_type["display_name"]
                conn.execute("""INSERT INTO domain_visits(
                    booking_id,visitor_name,visitor_email,host_name,destination,purpose,visit_date,visit_window,status,
                    qr_token_hash,qr_expires_at_utc,created_at_utc,updated_at_utc,visitor_type_id,visitor_type_name)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (booking_id, name.strip(), email.strip() if isinstance(email, str) else "", host.strip(), destination.strip(), purpose.strip() if isinstance(purpose, str) else "", visit_date, visit_window.strip(), "Invited", token_digest(token), iso_utc(expires), stamp, stamp, visitor_type_id, visitor_type_name))
                self._audit(conn, "InvitationCreated", booking_id, "Success", {"state": "Invited", "token": "hashed-only", "visitorTypeId": visitor_type_id, "visitorType": visitor_type_name})
                conn.execute("COMMIT")
            except DomainError:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise DomainError(409, "BOOKING_ID_EXISTS", "That booking ID already exists. Retrieve the booking or rotate its QR pass instead of creating a duplicate.") from exc
        return {"ok": True, "visit": self.get_visit(booking_id), "qrToken": token, "qrExpiresAtUtc": iso_utc(expires), "tokenPersistence": "hash-only; token returned once"}

    def amend_visit_schedule(self, booking_id: str, payload: object) -> dict:
        """Amend an invited synthetic visit's date/window while preserving its live QR token."""
        if not isinstance(payload, dict):
            raise DomainError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        visit_date = payload.get("visitDate")
        visit_window = payload.get("window")
        if not isinstance(visit_date, str) or not visit_date.strip() or len(visit_date.strip()) > 10:
            raise DomainError(400, "INVALID_VISIT_DATE", "Visit date is required and must use YYYY-MM-DD format.")
        if not isinstance(visit_window, str) or not visit_window.strip() or len(visit_window.strip()) > 32:
            raise DomainError(400, "INVALID_VISIT_WINDOW", "Visit window is required; choose a start and end time.")
        try:
            parsed_date = date.fromisoformat(visit_date)
        except ValueError as exc:
            raise DomainError(400, "INVALID_VISIT_DATE", "Visit date must use YYYY-MM-DD format.") from exc
        visit_window = canonicalize_window(visit_window)
        now = utc_now()
        local_today = now.astimezone(SCHOOL_TIMEZONE).date()
        if parsed_date < local_today:
            raise DomainError(400, "VISIT_DATE_IN_PAST", "An invitation cannot be amended to a past school-local date.")
        if parsed_date > local_today + timedelta(days=365):
            raise DomainError(400, "VISIT_DATE_TOO_FAR", "Invitations can be amended no more than 365 days in advance.")
        expires = self._expires_for(visit_date, visit_window)
        stamp = iso_utc(now)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM domain_visits WHERE booking_id=?", (booking_id.strip().upper(),)).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise DomainError(404, "VISIT_NOT_FOUND", "No visit exists for that booking ID.")
            if row["status"] != "Invited":
                conn.execute("ROLLBACK")
                raise DomainError(409, "AMEND_NOT_ALLOWED", "Only invited visits can be amended; checked-in, closed and cancelled visits are immutable here.")
            if not row["qr_token_hash"] or row["revoked_at_utc"]:
                conn.execute("ROLLBACK")
                raise DomainError(409, "QR_PASS_INACTIVE", "This invitation has no active QR pass. Rotate the pass before amending its schedule.")
            current_expiry = row["qr_expires_at_utc"]
            if not current_expiry or utc_string_to_datetime(current_expiry) <= now:
                conn.execute("ROLLBACK")
                raise DomainError(409, "QR_PASS_EXPIRED", "This QR pass has expired. Generate a new pass before amending the schedule.")
            before = {"visitDate": row["visit_date"], "window": row["visit_window"]}
            conn.execute(
                "UPDATE domain_visits SET visit_date=?,visit_window=?,qr_expires_at_utc=?,updated_at_utc=?,row_version=row_version+1 WHERE booking_id=?",
                (parsed_date.isoformat(), visit_window, iso_utc(expires), stamp, booking_id.strip().upper()),
            )
            self._audit(conn, "InvitationAmended", booking_id.strip().upper(), "Success", {
                "before": before, "after": {"visitDate": parsed_date.isoformat(), "window": visit_window},
                "qrTokenPreserved": True,
            })
            conn.execute("COMMIT")
        return {"ok": True, "visit": self.get_visit(booking_id.strip().upper()), "qrTokenPreserved": True,
                "message": "Visit schedule updated. The existing QR pass remains valid; re-sync the appointment to Genetec if it was previously linked."}

    def rotate_qr(self, booking_id: str) -> dict:
        token = secrets.token_urlsafe(32)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM domain_visits WHERE booking_id=?", (booking_id,)).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise DomainError(404, "VISIT_NOT_FOUND", "No visit exists for that booking ID.")
            if row["status"] != "Invited":
                conn.execute("ROLLBACK")
                raise DomainError(409, "PASS_ROTATION_NOT_ALLOWED", "QR pass rotation is permitted only for an invited visit.")
            expires = self._expires_for(row["visit_date"], row["visit_window"])
            stamp = iso_utc()
            conn.execute("UPDATE domain_visits SET qr_token_hash=?,qr_expires_at_utc=?,updated_at_utc=?,row_version=row_version+1 WHERE booking_id=?", (token_digest(token), iso_utc(expires), stamp, booking_id))
            self._audit(conn, "QrPassRotated", booking_id, "Success", {"previousTokenRevoked": True})
            conn.execute("COMMIT")
        return {"ok": True, "visit": self.get_visit(booking_id), "qrToken": token, "qrExpiresAtUtc": iso_utc(expires), "tokenPersistence": "hash-only; token returned once"}

    def cancel_visit(self, booking_id: str) -> dict:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM domain_visits WHERE booking_id=?", (booking_id,)).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise DomainError(404, "VISIT_NOT_FOUND", "No visit exists for that booking ID.")
            if row["status"] != "Invited":
                conn.execute("ROLLBACK")
                raise DomainError(409, "CANCEL_NOT_ALLOWED", "Only an invited visit can be cancelled by this endpoint.")
            stamp = iso_utc()
            conn.execute("UPDATE domain_visits SET status='Cancelled',qr_token_hash=NULL,revoked_at_utc=?,updated_at_utc=?,row_version=row_version+1 WHERE booking_id=?", (stamp, stamp, booking_id))
            self._audit(conn, "InvitationCancelled", booking_id, "Success", {"tokenRevoked": True})
            conn.execute("COMMIT")
        return {"ok": True, "visit": self.get_visit(booking_id), "tokenRevoked": True}

    def _token_row(self, token: object, conn: sqlite3.Connection) -> tuple[str, sqlite3.Row]:
        if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
            raise DomainError(400, "INVALID_QR_TOKEN", "The QR token format is invalid.")
        digest = token_digest(token)
        row = conn.execute("SELECT * FROM domain_visits WHERE qr_token_hash=?", (digest,)).fetchone()
        if not row:
            raise DomainError(404, "QR_NOT_RECOGNISED", "This QR pass is unknown, revoked or was rotated.")
        if row["status"] == "Cancelled" or row["revoked_at_utc"]:
            raise DomainError(410, "QR_REVOKED", "This QR pass has been revoked.")
        expiry = row["qr_expires_at_utc"]
        if not expiry or utc_string_to_datetime(expiry) <= utc_now():
            raise DomainError(410, "QR_EXPIRED", "This QR pass has expired.")
        return digest, row

    def _visit_window_bounds(self, row: sqlite3.Row) -> tuple[datetime, datetime]:
        start_time, end_time = parse_window(row["visit_window"])
        local_date = date.fromisoformat(row["visit_date"])
        start_local = datetime.combine(local_date, start_time, tzinfo=SCHOOL_TIMEZONE)
        end_local = datetime.combine(local_date, end_time, tzinfo=SCHOOL_TIMEZONE)
        return start_local, end_local

    def _in_visit_window(self, row: sqlite3.Row, now: datetime) -> bool:
        start_local, end_local = self._visit_window_bounds(row)
        # `now` arrives in UTC from the server clock. Compare both sides in the
        # school's explicit timezone rather than the host OS timezone.
        school_now = now.astimezone(SCHOOL_TIMEZONE)
        return start_local - timedelta(minutes=30) <= school_now <= end_local + timedelta(minutes=60)

    @staticmethod
    def _local_clock(value: datetime) -> str:
        return value.astimezone(SCHOOL_TIMEZONE).isoformat(timespec="minutes")

    def validate_qr(self, token: object) -> dict:
        with closing(self._connect()) as conn:
            digest, row = self._token_row(token, conn)
            now = utc_now()
            start_local, end_local = self._visit_window_bounds(row)
            opens_local = start_local - timedelta(minutes=30)
            closes_local = end_local + timedelta(minutes=60)
            can_check_in = row["status"] == "Invited" and self._in_visit_window(row, now)
            can_check_out = row["status"] in ("Inside", "Overdue")
            return {
                "ok": True, "visit": self._public(row),
                "canCheckIn": can_check_in, "canCheckOut": can_check_out,
                "tokenVerified": True, "tokenStoredAs": "SHA-256 hash only",
                "schoolTimeZone": SCHOOL_TIMEZONE_NAME,
                "schoolTimeNow": self._local_clock(now),
                "visitStartLocal": self._local_clock(start_local),
                "visitEndLocal": self._local_clock(end_local),
                "checkInOpensAtLocal": self._local_clock(opens_local),
                "checkInClosesAtLocal": self._local_clock(closes_local),
            }

    def qr_svg(self, token: object) -> bytes:
        # The QR renderer refuses unknown/revoked/expired passes and never logs raw token values.
        self.validate_qr(token)
        return make_svg(str(token))

    def transition_by_qr(self, token: object, action: str, event_id: object) -> dict:
        if action not in ("CheckIn", "CheckOut"):
            raise DomainError(400, "INVALID_ACTION", "Supported QR actions are CheckIn and CheckOut.")
        if not isinstance(event_id, str) or not EVENT_ID_RE.fullmatch(event_id):
            raise DomainError(400, "INVALID_EVENT_ID", "A stable eventId of 8–128 safe characters is required for idempotent retries.")
        # Validate token syntax before opening a write transaction.
        if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
            raise DomainError(400, "INVALID_QR_TOKEN", "The QR token format is invalid.")
        digest = token_digest(token)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous_event = conn.execute("SELECT * FROM domain_events WHERE event_id=?", (event_id,)).fetchone()
            if previous_event:
                if previous_event["action"] != action:
                    conn.execute("ROLLBACK")
                    raise DomainError(409, "EVENT_ID_REUSED", "This event ID was previously used for a different action.")
                if previous_event["qr_token_hash"] != digest:
                    conn.execute("ROLLBACK")
                    raise DomainError(409, "EVENT_ID_REUSED", "This event ID was previously used with a different QR pass.")
                row = conn.execute("SELECT * FROM domain_visits WHERE booking_id=?", (previous_event["booking_id"],)).fetchone()
                conn.execute("COMMIT")
                return {"ok": True, "alreadyApplied": True, "eventId": event_id, "bookingId": previous_event["booking_id"], "action": action, "status": previous_event["resulting_status"], "arrivalUtc": row["checked_in_at_utc"], "departureUtc": row["checked_out_at_utc"], "visit": self._public(row)}
            try:
                _, row = self._token_row(token, conn)
            except DomainError as exc:
                conn.execute("ROLLBACK")
                raise exc
            now = utc_now()
            booking_id = row["booking_id"]
            prior = row["status"]
            if action == "CheckIn":
                if prior != "Invited":
                    conn.execute("ROLLBACK")
                    raise DomainError(409, "CHECKIN_STATE_INVALID", "Only invited visits can be checked in; this visit is already checked in, closed or cancelled.")
                if not self._in_visit_window(row, now):
                    start_local, end_local = self._visit_window_bounds(row)
                    opens_local = start_local - timedelta(minutes=30)
                    closes_local = end_local + timedelta(minutes=60)
                    school_now = now.astimezone(SCHOOL_TIMEZONE)
                    conn.execute("ROLLBACK")
                    raise DomainError(409, "VISIT_WINDOW_CLOSED",
                        "This pass is outside its allowed check-in window.",
                        {"schoolTimeZone": SCHOOL_TIMEZONE_NAME, "schoolTimeNow": self._local_clock(school_now),
                         "visitDate": row["visit_date"], "visitWindow": row["visit_window"],
                         "visitStartLocal": self._local_clock(start_local), "visitEndLocal": self._local_clock(end_local),
                         "checkInOpensAtLocal": self._local_clock(opens_local), "checkInClosesAtLocal": self._local_clock(closes_local)})
                resulting = "Inside"
                conn.execute("UPDATE domain_visits SET status='Inside',checked_in_at_utc=?,updated_at_utc=?,row_version=row_version+1 WHERE booking_id=?", (iso_utc(now), iso_utc(now), booking_id))
            else:
                if prior not in ("Inside", "Overdue"):
                    conn.execute("ROLLBACK")
                    raise DomainError(409, "CHECKOUT_STATE_INVALID", "Only a currently checked-in visit can be checked out.")
                resulting = "Checked out"
                conn.execute("UPDATE domain_visits SET status='Checked out',checked_out_at_utc=?,updated_at_utc=?,row_version=row_version+1 WHERE booking_id=?", (iso_utc(now), iso_utc(now), booking_id))
            conn.execute("INSERT INTO domain_events(event_id,booking_id,action,occurred_at_utc,prior_status,resulting_status,qr_token_hash) VALUES(?,?,?,?,?,?,?)", (event_id, booking_id, action, iso_utc(now), prior, resulting, digest))
            self._audit(conn, action, booking_id, "Success", {"eventId": event_id, "from": prior, "to": resulting, "token": "hash-only"})
            conn.execute("COMMIT")
        final_visit = self.get_visit(booking_id)
        return {"ok": True, "alreadyApplied": False, "eventId": event_id, "bookingId": booking_id, "action": action, "status": resulting, "arrivalUtc": final_visit["arrivalUtc"], "departureUtc": final_visit["departureUtc"], "visit": final_visit}


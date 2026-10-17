"""Opt-in, local-only prototype for consented student attendance face matching.

This module deliberately stays disabled unless the school-policy gates are enabled.
Group-photo identification is a separate opt-in and is constrained to an approved
roster of individually enrolled, consented people. Group images are processed in
memory only; they are never used to enroll a face or persisted to disk.

This is a prototype adapter built around OpenCV-contrib LBPH, not a production
biometric system. A positive match is only a candidate: a staff operator must
confirm each attendance event. Do not use with real student data until the
school has approved the policy, consent process, threshold validation, security,
retention and operational acceptance.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

MAX_IMAGE_DATA_URL_CHARS = 2_500_000
MAX_IMAGE_DECODED_BYTES = 1_800_000
MAX_ENROLLMENT_SAMPLES = 5
MIN_ENROLLMENT_SAMPLES = 2
MAX_GROUP_FACES = 40
MAX_ROSTER_SIZE = 100
TICKET_TTL_SECONDS = 120
DEFAULT_LBPH_MAX_DISTANCE = 55.0
STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,95}$")
REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,119}$")
GENETEC_BRIDGE_DEFAULT = "http://127.0.0.1:5190"
GENETEC_CREDENTIAL_REF_MAX = 20
DATA_URL_RE = re.compile(r"^data:image/(?:jpeg|jpg|png|webp);base64,([A-Za-z0-9+/=\r\n]+)$", re.I)


class FaceAttendanceError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _student_feature_gate() -> bool:
    return _truthy_env("STAMFORD_FACE_ATTENDANCE_ENABLED") and _truthy_env("STAMFORD_BIOMETRIC_POLICY_APPROVED")


def _group_feature_gate() -> bool:
    return _student_feature_gate() and _truthy_env("STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED")


def _genetec_cardholder_face_sync_gate() -> bool:
    return _student_feature_gate() and _truthy_env("STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED")


def _require_genetec_cardholder_face_sync() -> None:
    _require_feature()
    if not _truthy_env("STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED"):
        raise FaceAttendanceError(
            403,
            "GENETEC_CARDHOLDER_FACE_SYNC_DISABLED",
            "Genetec Cardholder photo import is disabled. It requires STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED=1 after written school approval, in addition to the student face-attendance gates.",
        )


def _genetec_bridge_get(path: str, query: dict | None = None) -> dict:
    """Call only the loopback Genetec bridge; do not forward biometrics off-host."""
    base = os.environ.get("STAMFORD_GENETEC_BRIDGE_URL", GENETEC_BRIDGE_DEFAULT).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise FaceAttendanceError(503, "GENETEC_BRIDGE_URL_NOT_LOOPBACK", "Genetec biometric import is restricted to the local loopback bridge.")
    if not path.startswith("/api/genetec/") or ".." in path:
        raise FaceAttendanceError(500, "GENETEC_BRIDGE_PATH_INVALID", "Internal Genetec bridge route is invalid.")
    url = base + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "Origin": "http://127.0.0.1:8080"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read(3_000_001)
            if len(raw) > 3_000_000:
                raise FaceAttendanceError(413, "GENETEC_RESPONSE_TOO_LARGE", "The local Genetec response exceeds the import size limit.")
            parsed_json = json.loads(raw.decode("utf-8"))
    except FaceAttendanceError:
        raise
    except urllib.error.HTTPError as exc:
        try:
            error_payload = json.loads(exc.read(64_000).decode("utf-8"))
        except Exception:
            error_payload = {}
        message = error_payload.get("error") or error_payload.get("message") or "Genetec bridge rejected the request."
        code = error_payload.get("errorCode") or "GENETEC_BRIDGE_REJECTED"
        raise FaceAttendanceError(exc.code if 400 <= exc.code < 600 else 503, str(code)[:80], str(message)[:300]) from exc
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FaceAttendanceError(503, "GENETEC_BRIDGE_UNAVAILABLE", "The local Genetec bridge could not be reached or returned invalid JSON. Start the bridge and confirm its read-only status before retrying.") from exc
    if not isinstance(parsed_json, dict) or parsed_json.get("success") is not True:
        message = parsed_json.get("error") or parsed_json.get("message") or "Genetec bridge returned an unverified response."
        code = parsed_json.get("errorCode") or "GENETEC_RESPONSE_NOT_VERIFIED"
        raise FaceAttendanceError(503, str(code)[:80], str(message)[:300])
    return parsed_json


def _safe_genetec_credential_refs(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    safe = []
    for item in raw[:GENETEC_CREDENTIAL_REF_MAX]:
        if not isinstance(item, dict):
            continue
        guid_value = item.get("guid")
        try:
            guid_value = str(uuid.UUID(str(guid_value))) if guid_value else None
        except (ValueError, TypeError, AttributeError):
            guid_value = None
        name = item.get("name") if isinstance(item.get("name"), str) else None
        masked = item.get("maskedIdentifier") if isinstance(item.get("maskedIdentifier"), str) else None
        if name is not None:
            name = name.strip()[:120] or None
        if masked is not None:
            masked = masked.strip()[:24] or None
            # Only accept a masked suffix, never a raw credential number.
            if masked and not re.fullmatch(r"(?:•{2,}|\*{2,}|x{2,}|X{2,})[A-Za-z0-9]{0,4}", masked):
                masked = None
        active = item.get("active") if isinstance(item.get("active"), bool) else None
        if guid_value or name or masked:
            safe.append({"guid": guid_value, "name": name, "maskedIdentifier": masked, "active": active})
    return safe


def _opencv_module():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return None, None
    if not hasattr(cv2, "face") or not hasattr(cv2.face, "LBPHFaceRecognizer_create"):
        return None, None
    return cv2, np


def _require_feature(group: bool = False) -> None:
    if not _student_feature_gate():
        raise FaceAttendanceError(
            403,
            "FACE_ATTENDANCE_DISABLED",
            "Face attendance is disabled. It requires both STAMFORD_FACE_ATTENDANCE_ENABLED=1 and STAMFORD_BIOMETRIC_POLICY_APPROVED=1 after school approval.",
        )
    if group and not _group_feature_gate():
        raise FaceAttendanceError(
            403,
            "GROUP_FACE_IDENTIFICATION_NOT_APPROVED",
            "Group-photo identity matching is not enabled by the approved baseline. It requires a separately approved scope change and STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED=1. Group images cannot enroll people.",
        )
    if os.name != "nt":
        raise FaceAttendanceError(503, "WINDOWS_DPAPI_REQUIRED", "Biometric sample storage requires Windows DPAPI CurrentUser protection on the local school workstation.")
    cv2, _ = _opencv_module()
    if cv2 is None:
        raise FaceAttendanceError(503, "OPENCV_CONTRIB_REQUIRED", "The optional OpenCV-contrib face module is not installed. On the approved Windows workstation, install requirements-face-attendance.txt after the school approves the pilot.")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime | None = None) -> str:
    return (value or _now_utc()).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_ref(value: object, field: str) -> str:
    if not isinstance(value, str) or not REFERENCE_RE.fullmatch(value.strip()):
        raise FaceAttendanceError(400, "APPROVAL_REFERENCE_REQUIRED", f"{field} must be a non-empty audit reference (3–120 safe characters).")
    return value.strip()


def _validate_id(value: object, field: str = "studentId") -> str:
    if not isinstance(value, str) or not STUDENT_ID_RE.fullmatch(value.strip()):
        raise FaceAttendanceError(400, "INVALID_STUDENT_ID", f"{field} must contain 2–64 letters, digits, dots, underscores or hyphens.")
    return value.strip().upper()


def _validate_session(value: object) -> str:
    if not isinstance(value, str) or not SESSION_ID_RE.fullmatch(value.strip()):
        raise FaceAttendanceError(400, "INVALID_SESSION_ID", "sessionId must be a stable 2–96 character event/session reference.")
    return value.strip()


def _dpapi_protect(raw: bytes, description: str) -> bytes:
    if os.name != "nt":
        raise FaceAttendanceError(503, "WINDOWS_DPAPI_REQUIRED", "Biometric templates may only be stored on the approved Windows workstation using DPAPI.")

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = DATA_BLOB(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DATA_BLOB()
    from ctypes import wintypes
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptProtectData(
        ctypes.byref(source), description, None, None, None, 0x1, ctypes.byref(output)
    )
    if not ok:
        raise FaceAttendanceError(500, "BIOMETRIC_ENCRYPTION_FAILED", "Windows could not protect the biometric sample with DPAPI.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def _dpapi_unprotect(protected: bytes) -> bytes:
    if os.name != "nt":
        raise FaceAttendanceError(503, "WINDOWS_DPAPI_REQUIRED", "Biometric templates may only be read by the same Windows user that enrolled them.")

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = (ctypes.c_ubyte * len(protected)).from_buffer_copy(protected)
    source = DATA_BLOB(len(protected), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DATA_BLOB()
    from ctypes import wintypes
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    description = wintypes.LPWSTR()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source), ctypes.byref(description), None, None, None, 0x1, ctypes.byref(output)
    )
    if not ok:
        raise FaceAttendanceError(500, "BIOMETRIC_DECRYPTION_FAILED", "Windows could not decrypt the biometric sample. Use the enrolling Windows account on this workstation.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))


def _image_bytes(data_url: object) -> bytes:
    if not isinstance(data_url, str) or len(data_url) > MAX_IMAGE_DATA_URL_CHARS:
        raise FaceAttendanceError(400, "INVALID_IMAGE", "Image must be a camera JPEG/PNG/WebP data URL within the prototype size limit.")
    match = DATA_URL_RE.fullmatch(data_url.strip())
    if not match:
        raise FaceAttendanceError(400, "INVALID_IMAGE", "Image must be a JPEG, PNG or WebP data URL.")
    try:
        raw = base64.b64decode(match.group(1), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise FaceAttendanceError(400, "INVALID_IMAGE", "Camera image data is not valid base64.") from exc
    if not raw or len(raw) > MAX_IMAGE_DECODED_BYTES:
        raise FaceAttendanceError(413, "IMAGE_TOO_LARGE", "Decoded image exceeds the local prototype image limit.")
    return raw


def _extract_faces(data_url: object, max_faces: int = MAX_GROUP_FACES) -> list[bytes]:
    _require_feature()
    cv2, np = _opencv_module()
    raw = _image_bytes(data_url)
    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        raise FaceAttendanceError(400, "INVALID_IMAGE", "The camera image could not be decoded.")
    height, width = img.shape[:2]
    if width > 1920 or height > 1440 or width < 160 or height < 120:
        raise FaceAttendanceError(400, "IMAGE_DIMENSIONS_INVALID", "Camera image must be between 160×120 and 1920×1440 pixels.")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cascade_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        raise FaceAttendanceError(503, "FACE_DETECTOR_UNAVAILABLE", "The local OpenCV face detector could not be loaded.")
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
    if len(faces) == 0:
        raise FaceAttendanceError(422, "NO_FACE_DETECTED", "No face was detected. Improve lighting and frame one face fully.")
    if len(faces) > max_faces:
        raise FaceAttendanceError(413, "TOO_MANY_FACES", f"Detected more than the allowed {max_faces} faces for one scan.")
    output: list[bytes] = []
    for x, y, w, h in sorted(faces, key=lambda box: (int(box[1]), int(box[0]))):
        pad_x = int(w * 0.12)
        pad_y = int(h * 0.12)
        x0, y0 = max(0, int(x) - pad_x), max(0, int(y) - pad_y)
        x1, y1 = min(gray.shape[1], int(x + w + pad_x)), min(gray.shape[0], int(y + h + pad_y))
        crop = gray[y0:y1, x0:x1]
        crop = cv2.equalizeHist(crop)
        crop = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".png", crop)
        if not ok:
            raise FaceAttendanceError(500, "FACE_PREPROCESS_FAILED", "The local face crop could not be prepared.")
        output.append(encoded.tobytes())
    return output


def _decode_crop(crop_bytes: bytes):
    cv2, np = _opencv_module()
    if cv2 is None:
        raise FaceAttendanceError(503, "OPENCV_CONTRIB_REQUIRED", "OpenCV-contrib is required for face matching.")
    image = cv2.imdecode(np.frombuffer(crop_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FaceAttendanceError(500, "BIOMETRIC_SAMPLE_CORRUPT", "An encrypted stored face sample could not be decoded.")
    return image


def _event_type(value: object) -> str:
    if value not in ("CheckIn", "CheckOut"):
        raise FaceAttendanceError(400, "INVALID_ATTENDANCE_ACTION", "eventType must be CheckIn or CheckOut.")
    return str(value)


class FaceAttendanceStore:
    """Local biometric enrollments and attendance ledger, gated by school approval."""

    def __init__(self, db_path: Path, *, protector=None, unprotector=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._protect = protector or _dpapi_protect
        self._unprotect = unprotector or _dpapi_unprotect
        self._lock = threading.RLock()
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
                CREATE TABLE IF NOT EXISTS face_enrollments (
                    student_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    class_name TEXT NOT NULL,
                    consent_reference TEXT NOT NULL,
                    school_approval_reference TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    enrolled_at_utc TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    genetec_cardholder_guid TEXT,
                    genetec_credential_refs_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS face_samples (
                    student_id TEXT NOT NULL REFERENCES face_enrollments(student_id),
                    sample_no INTEGER NOT NULL,
                    protected_sample BLOB NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY(student_id,sample_no)
                );
                CREATE TABLE IF NOT EXISTS face_match_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL CHECK(mode IN ('student','group')),
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('CheckIn','CheckOut')),
                    student_id TEXT NOT NULL REFERENCES face_enrollments(student_id),
                    group_event_id TEXT NOT NULL DEFAULT '',
                    distance REAL NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    consumed_at_utc TEXT
                );
                CREATE TABLE IF NOT EXISTS student_attendance_events (
                    attendance_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL REFERENCES face_enrollments(student_id),
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('CheckIn','CheckOut')),
                    group_event_id TEXT NOT NULL DEFAULT '',
                    occurred_at_utc TEXT NOT NULL,
                    operator_name TEXT NOT NULL,
                    ticket_id TEXT NOT NULL UNIQUE REFERENCES face_match_tickets(ticket_id),
                    model_distance REAL NOT NULL,
                    genetec_cardholder_guid TEXT,
                    genetec_credential_refs_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(student_id,session_id,event_type,group_event_id)
                );
                CREATE TABLE IF NOT EXISTS face_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at_utc TEXT NOT NULL,
                    action TEXT NOT NULL,
                    student_id TEXT,
                    session_id TEXT,
                    outcome TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_face_samples_student ON face_samples(student_id);
                CREATE INDEX IF NOT EXISTS idx_attendance_session ON student_attendance_events(session_id,group_event_id);
                CREATE INDEX IF NOT EXISTS idx_face_tickets_expiry ON face_match_tickets(expires_at_utc);
            """)
            # Backward-compatible additive migrations for existing Milestone 5.2 biometric databases.
            for table, column, definition in (
                ("face_enrollments", "genetec_cardholder_guid", "TEXT"),
                ("face_enrollments", "genetec_credential_refs_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("student_attendance_events", "genetec_cardholder_guid", "TEXT"),
                ("student_attendance_events", "genetec_credential_refs_json", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_face_enrollments_genetec_guid ON face_enrollments(genetec_cardholder_guid) WHERE genetec_cardholder_guid IS NOT NULL")

    def _audit(self, conn: sqlite3.Connection, action: str, student_id: str | None, session_id: str | None, outcome: str, details: dict | None = None):
        conn.execute(
            "INSERT INTO face_audit(occurred_at_utc,action,student_id,session_id,outcome,detail_json) VALUES(?,?,?,?,?,?)",
            (_iso_utc(), action, student_id, session_id, outcome, json.dumps(details or {}, separators=(",", ":"))),
        )

    def status(self) -> dict:
        cv2, _ = _opencv_module()
        with closing(self._connect()) as conn:
            active_students = conn.execute("SELECT COUNT(*) AS n FROM face_enrollments WHERE active=1").fetchone()["n"]
        return {
            "ok": True,
            "studentFaceAttendanceEnabled": _student_feature_gate(),
            "groupFaceIdentificationEnabled": _group_feature_gate(),
            "schoolPolicyApprovedFlag": _truthy_env("STAMFORD_BIOMETRIC_POLICY_APPROVED"),
            "studentFeatureFlag": _truthy_env("STAMFORD_FACE_ATTENDANCE_ENABLED"),
            "groupApprovalFlag": _truthy_env("STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED"),
            "genetecCardholderFaceSyncEnabled": _genetec_cardholder_face_sync_gate(),
            "genetecCardholderFaceSyncApprovedFlag": _truthy_env("STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED"),
            "engineAvailable": cv2 is not None,
            "encryptedStorageAvailable": os.name == "nt",
            "activeEnrolledStudents": int(active_students),
            "rawImagesPersisted": False,
            "groupImagesPersisted": False,
            "recognitionResultRequiresHumanConfirmation": True,
            "authentication": "not-configured-prototype-only",
        }

    def _assert_ready(self, group: bool = False) -> None:
        _require_feature(group)

    def list_genetec_cardholders(self, search: object) -> dict:
        _require_genetec_cardholder_face_sync()
        if not isinstance(search, str) or len(search.strip()) < 2 or len(search.strip()) > 80:
            raise FaceAttendanceError(400, "CARDHOLDER_SEARCH_REQUIRED", "Enter at least two characters to search Genetec Cardholders.")
        response = _genetec_bridge_get("/api/genetec/cardholders", {"search": search.strip(), "limit": "25"})
        source = response.get("cardholders")
        if not isinstance(source, list):
            raise FaceAttendanceError(503, "GENETEC_CARDHOLDER_RESPONSE_INVALID", "The bridge did not return a valid Cardholder list.")
        cardholders = []
        for item in source[:50]:
            if not isinstance(item, dict):
                continue
            try:
                guid = str(uuid.UUID(str(item.get("cardholderGuid"))))
            except (ValueError, TypeError, AttributeError):
                continue
            credential_refs = _safe_genetec_credential_refs(item.get("credentialReferences"))
            cardholders.append({
                "cardholderGuid": guid,
                "displayName": str(item.get("displayName") or "")[:160],
                "firstName": str(item.get("firstName") or "")[:100],
                "lastName": str(item.get("lastName") or "")[:100],
                "externalId": str(item.get("externalId") or "")[:160],
                "photoAvailable": item.get("photoAvailable") is True,
                "credentialReferences": credential_refs,
                "rawCredentialNumbersReturned": False,
            })
        return {"ok": True, "source": "Genetec Security Center", "readOnly": True, "cardholders": cardholders, "rawCredentialNumbersReturned": False}

    def enroll_from_genetec(self, payload: object) -> dict:
        _require_genetec_cardholder_face_sync()
        if not isinstance(payload, dict):
            raise FaceAttendanceError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        if payload.get("guardianConsent") is not True:
            raise FaceAttendanceError(403, "GUARDIAN_CONSENT_REQUIRED", "A recorded consent/eligibility decision is required before importing a Genetec portrait for biometric attendance.")
        try:
            cardholder_guid = str(uuid.UUID(str(payload.get("cardholderGuid"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise FaceAttendanceError(400, "INVALID_CARDHOLDER_GUID", "Select a Cardholder returned by the Genetec bridge.") from exc
        extra_images = payload.get("images")
        if not isinstance(extra_images, list) or not 1 <= len(extra_images) <= MAX_ENROLLMENT_SAMPLES - 1:
            raise FaceAttendanceError(400, "GENETEC_IMPORT_CAMERA_SAMPLES_REQUIRED", "Capture 1–4 additional individual camera samples. The selected Genetec portrait counts as the first sample.")
        if any(not isinstance(item, str) for item in extra_images):
            raise FaceAttendanceError(400, "INVALID_IMAGE", "Each camera sample must be an image data URL.")

        response = _genetec_bridge_get("/api/genetec/cardholder-photo", {"cardholderGuid": cardholder_guid})
        profile = response.get("cardholder")
        if not isinstance(profile, dict):
            raise FaceAttendanceError(503, "GENETEC_CARDHOLDER_PROFILE_INVALID", "The Genetec bridge did not return the selected Cardholder profile.")
        try:
            returned_guid = str(uuid.UUID(str(profile.get("cardholderGuid"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise FaceAttendanceError(503, "GENETEC_CARDHOLDER_PROFILE_INVALID", "The Genetec bridge returned an invalid Cardholder GUID.") from exc
        if returned_guid.lower() != cardholder_guid.lower():
            raise FaceAttendanceError(409, "GENETEC_CARDHOLDER_MISMATCH", "The returned photo does not belong to the selected Cardholder GUID.")
        mime = response.get("imageMimeType")
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            raise FaceAttendanceError(415, "GENETEC_PHOTO_FORMAT_UNSUPPORTED", "The imported Genetec portrait must be JPEG, PNG or WebP.")
        encoded = response.get("imageBase64")
        if not isinstance(encoded, str) or len(encoded) > MAX_IMAGE_DATA_URL_CHARS:
            raise FaceAttendanceError(413, "GENETEC_PHOTO_TOO_LARGE", "The Genetec portrait exceeds the local import limit.")
        try:
            photo_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise FaceAttendanceError(503, "GENETEC_PHOTO_INVALID", "The Genetec bridge returned invalid image data.") from exc
        if not photo_bytes or len(photo_bytes) > MAX_IMAGE_DECODED_BYTES:
            raise FaceAttendanceError(413, "GENETEC_PHOTO_TOO_LARGE", "The Genetec portrait must be within the 1.8 MB local import limit.")
        portrait_url = f"data:{mime};base64,{base64.b64encode(photo_bytes).decode('ascii')}"

        display_name = payload.get("displayName") or profile.get("displayName") or ""
        student_id = payload.get("studentId")
        if not student_id:
            external_id = profile.get("externalId")
            # Genetec external identifiers are not guaranteed to follow the VMS student-ID
            # grammar. Reuse only a safe, already-valid school identifier; otherwise use
            # a deterministic GUID-derived key and let staff map the official student ID.
            if isinstance(external_id, str) and STUDENT_ID_RE.fullmatch(external_id.strip()):
                student_id = external_id.strip()
            else:
                student_id = "GC-" + cardholder_guid.replace("-", "").upper()
        credential_refs = _safe_genetec_credential_refs(profile.get("credentialReferences"))
        enrollment_payload = dict(payload)
        enrollment_payload.update({
            "studentId": student_id,
            "displayName": display_name,
            "images": [portrait_url] + extra_images,
            "genetecCardholderGuid": cardholder_guid,
            "genetecCredentialReferences": credential_refs,
        })
        # The regular enrollment path independently re-detects exactly one face per image,
        # enforces the standard consent references and encrypts only normalized crops.
        result = self.enroll(enrollment_payload)
        result.update({
            "identitySource": "GenetecCardholder",
            "genetecCardholderGuid": cardholder_guid,
            "genetecCredentialReferenceCount": len(credential_refs),
            "rawCredentialNumbersReturned": False,
            "genetecPhotoPersistedAsRawImage": False,
            "message": "The Genetec portrait and separate live samples were processed locally; only encrypted face crops were retained. Attendance remains subject to staff confirmation.",
        })
        return result

    def enroll(self, payload: object) -> dict:
        self._assert_ready()
        if not isinstance(payload, dict):
            raise FaceAttendanceError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        if payload.get("guardianConsent") is not True:
            raise FaceAttendanceError(403, "GUARDIAN_CONSENT_REQUIRED", "A recorded consent/eligibility decision is required before enrollment.")
        student_id = _validate_id(payload.get("studentId"))
        name = payload.get("displayName")
        class_name = payload.get("className")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise FaceAttendanceError(400, "INVALID_NAME", "displayName is required and must be 1–100 characters.")
        if not isinstance(class_name, str) or not class_name.strip() or len(class_name.strip()) > 50:
            raise FaceAttendanceError(400, "INVALID_CLASS", "className is required and must be 1–50 characters.")
        consent_ref = _validate_ref(payload.get("consentReference"), "consentReference")
        approval_ref = _validate_ref(payload.get("schoolApprovalReference"), "schoolApprovalReference")
        approved_by = payload.get("approvedBy")
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by.strip()) > 100:
            raise FaceAttendanceError(400, "APPROVER_REQUIRED", "approvedBy must identify the authorised staff approver.")
        images = payload.get("images")
        if not isinstance(images, list) or not MIN_ENROLLMENT_SAMPLES <= len(images) <= MAX_ENROLLMENT_SAMPLES:
            raise FaceAttendanceError(400, "ENROLLMENT_SAMPLE_COUNT", f"Provide {MIN_ENROLLMENT_SAMPLES}–{MAX_ENROLLMENT_SAMPLES} separate single-person samples. Group photos cannot be used for enrollment.")
        if any(not isinstance(item, str) for item in images):
            raise FaceAttendanceError(400, "INVALID_IMAGE", "Each enrollment sample must be an image data URL.")
        genetec_cardholder_guid = payload.get("genetecCardholderGuid")
        if genetec_cardholder_guid:
            try:
                genetec_cardholder_guid = str(uuid.UUID(str(genetec_cardholder_guid)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise FaceAttendanceError(400, "INVALID_CARDHOLDER_GUID", "genetecCardholderGuid must be a GUID returned by Genetec.") from exc
        genetec_credential_refs = _safe_genetec_credential_refs(payload.get("genetecCredentialReferences"))
        # Each capture must contain exactly one detected face. A group image is rejected.
        samples = []
        for item in images:
            try:
                samples.append(_extract_faces(item, max_faces=1))
            except FaceAttendanceError as exc:
                if exc.code == "TOO_MANY_FACES":
                    raise FaceAttendanceError(422, "GROUP_PHOTO_ENROLLMENT_BLOCKED", "Enrollment accepts separate individual captures only. A group photograph cannot be used to enroll any person.") from exc
                raise
        crops = []
        for item, face_crops in zip(images, samples):
            # _extract_faces(max_faces=1) rejects only >1; fail if 0 already handled by function.
            crops.append(face_crops[0])
        protected = [self._protect(crop, f"Stamford VMS face sample {student_id}") for crop in crops]
        now = _iso_utc()
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT active FROM face_enrollments WHERE student_id=?", (student_id,)).fetchone()
            if existing and existing["active"]:
                conn.execute("ROLLBACK")
                raise FaceAttendanceError(409, "STUDENT_ALREADY_ENROLLED", "This student already has an active face enrollment. Use an approved re-enrollment workflow; do not overwrite it silently.")
            if genetec_cardholder_guid:
                linked = conn.execute(
                    "SELECT student_id FROM face_enrollments WHERE genetec_cardholder_guid=? AND student_id<>?",
                    (genetec_cardholder_guid, student_id),
                ).fetchone()
                if linked:
                    conn.execute("ROLLBACK")
                    raise FaceAttendanceError(409, "GENETEC_CARDHOLDER_ALREADY_LINKED", "This Genetec Cardholder is already linked to another attendance profile. Reconcile the existing mapping instead of creating a duplicate.")
            if existing:
                conn.execute("UPDATE face_enrollments SET display_name=?,class_name=?,consent_reference=?,school_approval_reference=?,approved_by=?,enrolled_at_utc=?,active=1,genetec_cardholder_guid=?,genetec_credential_refs_json=? WHERE student_id=?", (name.strip(), class_name.strip(), consent_ref, approval_ref, approved_by.strip(), now, genetec_cardholder_guid, json.dumps(genetec_credential_refs, separators=(",", ":")), student_id))
                conn.execute("DELETE FROM face_samples WHERE student_id=?", (student_id,))
            else:
                try:
                    conn.execute("INSERT INTO face_enrollments(student_id,display_name,class_name,consent_reference,school_approval_reference,approved_by,enrolled_at_utc,active,genetec_cardholder_guid,genetec_credential_refs_json) VALUES(?,?,?,?,?,?,?,1,?,?)", (student_id, name.strip(), class_name.strip(), consent_ref, approval_ref, approved_by.strip(), now, genetec_cardholder_guid, json.dumps(genetec_credential_refs, separators=(",", ":"))))
                except sqlite3.IntegrityError as exc:
                    conn.execute("ROLLBACK")
                    if genetec_cardholder_guid:
                        raise FaceAttendanceError(409, "GENETEC_CARDHOLDER_ALREADY_LINKED", "This Genetec Cardholder is already linked to another attendance profile. Reconcile the existing mapping instead of creating a duplicate.") from exc
                    raise
            conn.executemany("INSERT INTO face_samples(student_id,sample_no,protected_sample,created_at_utc) VALUES(?,?,?,?)", [(student_id, idx + 1, blob, now) for idx, blob in enumerate(protected)])
            self._audit(conn, "FaceEnrollmentCreated", student_id, None, "Success", {"sampleCount": len(protected), "consentReference": consent_ref, "schoolApprovalReference": approval_ref, "approvedBy": approved_by.strip(), "rawImagesPersisted": False, "identitySource": "GenetecCardholder" if genetec_cardholder_guid else "Manual", "genetecCardholderGuid": genetec_cardholder_guid, "genetecCredentialReferenceCount": len(genetec_credential_refs)})
            conn.execute("COMMIT")
        return {"ok": True, "studentId": student_id, "displayName": name.strip(), "className": class_name.strip(), "sampleCount": len(protected), "enrolledAtUtc": now, "rawImagesPersisted": False, "identitySource": "GenetecCardholder" if genetec_cardholder_guid else "Manual", "genetecCardholderGuid": genetec_cardholder_guid, "genetecCredentialReferenceCount": len(genetec_credential_refs), "message": "Encrypted individual face samples stored locally. Capture frames and group photos are not retained."}

    def list_students(self) -> dict:
        self._assert_ready()
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT student_id,display_name,class_name,enrolled_at_utc,genetec_cardholder_guid,genetec_credential_refs_json FROM face_enrollments WHERE active=1 ORDER BY display_name COLLATE NOCASE").fetchall()
        students = []
        for r in rows:
            try: credential_refs = _safe_genetec_credential_refs(json.loads(r["genetec_credential_refs_json"] or "[]"))
            except Exception: credential_refs = []
            students.append({"studentId": r["student_id"], "displayName": r["display_name"], "className": r["class_name"], "enrolledAtUtc": r["enrolled_at_utc"], "genetecCardholderGuid": r["genetec_cardholder_guid"], "genetecCredentialReferences": credential_refs})
        return {"ok": True, "students": students}

    def revoke_enrollment(self, student_id: object, payload: object) -> dict:
        self._assert_ready()
        student_id = _validate_id(student_id)
        if not isinstance(payload, dict):
            raise FaceAttendanceError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        actor = payload.get("approvedBy")
        reference = _validate_ref(payload.get("withdrawalReference"), "withdrawalReference")
        if not isinstance(actor, str) or not actor.strip():
            raise FaceAttendanceError(400, "APPROVER_REQUIRED", "approvedBy must identify the authorised staff member processing withdrawal.")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT active FROM face_enrollments WHERE student_id=?", (student_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise FaceAttendanceError(404, "STUDENT_NOT_FOUND", "No face enrollment exists for this student.")
            conn.execute("UPDATE face_enrollments SET active=0 WHERE student_id=?", (student_id,))
            conn.execute("DELETE FROM face_samples WHERE student_id=?", (student_id,))
            self._audit(conn, "FaceEnrollmentRevoked", student_id, None, "Success", {"withdrawalReference": reference, "approvedBy": actor.strip(), "samplesDeleted": True})
            conn.execute("COMMIT")
        return {"ok": True, "studentId": student_id, "enrollmentActive": False, "samplesDeleted": True}

    def _training_data(self, student_ids: list[str] | None = None):
        self._assert_ready()
        cv2, np = _opencv_module()
        assert cv2 is not None and np is not None
        with closing(self._connect()) as conn:
            if student_ids is None:
                profiles = conn.execute("SELECT student_id,display_name,class_name,genetec_cardholder_guid,genetec_credential_refs_json FROM face_enrollments WHERE active=1 ORDER BY student_id").fetchall()
            elif not student_ids:
                profiles = []
            else:
                placeholders = ",".join("?" for _ in student_ids)
                profiles = conn.execute(f"SELECT student_id,display_name,class_name,genetec_cardholder_guid,genetec_credential_refs_json FROM face_enrollments WHERE active=1 AND student_id IN ({placeholders}) ORDER BY student_id", tuple(student_ids)).fetchall()
            labels_by_id = {row["student_id"]: idx + 1 for idx, row in enumerate(profiles)}
            id_by_label = {idx + 1: row["student_id"] for idx, row in enumerate(profiles)}
            profile_by_id = {}
            for row in profiles:
                try: credential_refs = _safe_genetec_credential_refs(json.loads(row["genetec_credential_refs_json"] or "[]"))
                except Exception: credential_refs = []
                profile_by_id[row["student_id"]] = {"studentId": row["student_id"], "displayName": row["display_name"], "className": row["class_name"], "genetecCardholderGuid": row["genetec_cardholder_guid"], "genetecCredentialReferences": credential_refs}
            if not profiles:
                raise FaceAttendanceError(409, "NO_CONSENTED_ENROLLMENTS", "No active, approved, consented face enrollments are available for this scope.")
            images, labels = [], []
            for row in profiles:
                samples = conn.execute("SELECT protected_sample FROM face_samples WHERE student_id=? ORDER BY sample_no", (row["student_id"],)).fetchall()
                for sample in samples:
                    raw = self._unprotect(bytes(sample["protected_sample"]))
                    images.append(_decode_crop(raw))
                    labels.append(labels_by_id[row["student_id"]])
        if not images:
            raise FaceAttendanceError(409, "NO_CONSENTED_ENROLLMENTS", "No encrypted individual samples are available for the selected roster.")
        recognizer = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
        recognizer.train(images, np.array(labels, dtype=np.int32))
        try:
            max_distance = float(os.environ.get("STAMFORD_FACE_LBPH_MAX_DISTANCE", str(DEFAULT_LBPH_MAX_DISTANCE)))
        except ValueError:
            max_distance = DEFAULT_LBPH_MAX_DISTANCE
        if not 10.0 <= max_distance <= 120.0:
            max_distance = DEFAULT_LBPH_MAX_DISTANCE
        return recognizer, id_by_label, profile_by_id, max_distance

    def _new_ticket(self, mode: str, session_id: str, event_type: str, student_id: str, group_event_id: str, distance: float) -> str:
        ticket = secrets.token_urlsafe(24)
        now = _now_utc()
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO face_match_tickets(ticket_id,mode,session_id,event_type,student_id,group_event_id,distance,created_at_utc,expires_at_utc) VALUES(?,?,?,?,?,?,?,?,?)", (ticket, mode, session_id, event_type, student_id, group_event_id, float(distance), _iso_utc(now), _iso_utc(now + timedelta(seconds=TICKET_TTL_SECONDS))))
        return ticket

    def match_student(self, payload: object) -> dict:
        self._assert_ready()
        if not isinstance(payload, dict):
            raise FaceAttendanceError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        event_type = _event_type(payload.get("eventType"))
        session_id = _validate_session(payload.get("sessionId"))
        faces = _extract_faces(payload.get("imageData"), max_faces=2)
        if len(faces) != 1:
            raise FaceAttendanceError(422, "SINGLE_FACE_REQUIRED", "Student attendance expects exactly one face in the camera frame. Use the group flow only for an approved roster and group event.")
        recognizer, id_by_label, profiles, max_distance = self._training_data()
        label, distance = recognizer.predict(_decode_crop(faces[0]))
        student_id = id_by_label.get(int(label))
        if not student_id or float(distance) > max_distance:
            return {"ok": True, "matched": False, "reason": "NO_CONFIDENT_MATCH", "message": "No eligible enrolled face matched the configured threshold. Use the normal staff/QR attendance path; do not force a match.", "humanConfirmationRequired": True}
        ticket = self._new_ticket("student", session_id, event_type, student_id, "", float(distance))
        return {"ok": True, "matched": True, "candidate": profiles[student_id], "distance": round(float(distance), 3), "ticketId": ticket, "ticketExpiresInSeconds": TICKET_TTL_SECONDS, "humanConfirmationRequired": True, "rawImagePersisted": False, "message": "Candidate only. An authorised staff operator must confirm the identity before recording attendance."}

    def match_group(self, payload: object) -> dict:
        self._assert_ready(group=True)
        if not isinstance(payload, dict):
            raise FaceAttendanceError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        event_type = _event_type(payload.get("eventType"))
        group_event_id = _validate_session(payload.get("groupEventId"))
        session_id = _validate_session(payload.get("sessionId", group_event_id))
        roster_raw = payload.get("studentIds")
        if not isinstance(roster_raw, list) or not roster_raw or len(roster_raw) > MAX_ROSTER_SIZE:
            raise FaceAttendanceError(400, "INVALID_ROSTER", f"studentIds must contain 1–{MAX_ROSTER_SIZE} pre-enrolled roster IDs.")
        roster_ids = list(dict.fromkeys(_validate_id(item) for item in roster_raw))
        faces = _extract_faces(payload.get("imageData"), max_faces=MAX_GROUP_FACES)
        recognizer, id_by_label, profiles, max_distance = self._training_data(roster_ids)
        candidates = []
        seen_students: set[str] = set()
        for index, crop in enumerate(faces, start=1):
            label, distance = recognizer.predict(_decode_crop(crop))
            student_id = id_by_label.get(int(label))
            if not student_id or float(distance) > max_distance:
                candidates.append({"faceIndex": index, "matched": False, "reason": "NO_CONFIDENT_MATCH", "message": "No eligible roster match; staff must reconcile manually."})
                continue
            if student_id in seen_students:
                candidates.append({"faceIndex": index, "matched": False, "reason": "DUPLICATE_CANDIDATE", "message": "The same student was suggested for more than one face; this face must be reconciled manually."})
                continue
            seen_students.add(student_id)
            ticket = self._new_ticket("group", session_id, event_type, student_id, group_event_id, float(distance))
            candidates.append({"faceIndex": index, "matched": True, "candidate": profiles[student_id], "distance": round(float(distance), 3), "ticketId": ticket, "ticketExpiresInSeconds": TICKET_TTL_SECONDS, "humanConfirmationRequired": True})
        return {"ok": True, "groupEventId": group_event_id, "eventType": event_type, "detectedFaceCount": len(faces), "candidates": candidates, "groupImagePersisted": False, "groupImageUsedForEnrollment": False, "humanRosterReconciliationRequired": True, "message": "Matches are limited to this approved, consented roster. Review every candidate; no attendance is recorded until an operator explicitly confirms the batch."}

    def _confirm_in_tx(self, conn: sqlite3.Connection, ticket_id: object, student_id: object, operator: object, expected_mode: str, expected_session: str | None, expected_group_id: str | None):
        if not isinstance(ticket_id, str) or not 24 <= len(ticket_id) <= 80:
            raise FaceAttendanceError(400, "INVALID_MATCH_TICKET", "ticketId is invalid.")
        student_id = _validate_id(student_id)
        if not isinstance(operator, str) or not operator.strip() or len(operator.strip()) > 100:
            raise FaceAttendanceError(400, "OPERATOR_REQUIRED", "An authorised staff operator name is required for audit.")
        row = conn.execute("SELECT * FROM face_match_tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if row is None:
            raise FaceAttendanceError(404, "MATCH_TICKET_NOT_FOUND", "Match ticket not found; run a fresh match before confirming.")
        if row["consumed_at_utc"]:
            raise FaceAttendanceError(409, "MATCH_TICKET_ALREADY_USED", "This match ticket has already been used.")
        if datetime.fromisoformat(row["expires_at_utc"].replace("Z", "+00:00")) <= _now_utc():
            raise FaceAttendanceError(409, "MATCH_TICKET_EXPIRED", "The face-match confirmation window expired. Scan again before confirming.")
        if row["mode"] != expected_mode or row["student_id"] != student_id:
            raise FaceAttendanceError(409, "MATCH_TICKET_MISMATCH", "The confirmed student does not match the candidate tied to this ticket.")
        if expected_session is not None and row["session_id"] != expected_session:
            raise FaceAttendanceError(409, "MATCH_SESSION_MISMATCH", "The match belongs to another attendance session.")
        if expected_group_id is not None and row["group_event_id"] != expected_group_id:
            raise FaceAttendanceError(409, "MATCH_GROUP_MISMATCH", "The match belongs to another group event.")
        enrol = conn.execute("SELECT active FROM face_enrollments WHERE student_id=?", (student_id,)).fetchone()
        if enrol is None or not enrol["active"]:
            raise FaceAttendanceError(409, "ENROLLMENT_INACTIVE", "This student's biometric enrollment is not active.")
        duplicate = conn.execute("SELECT attendance_id FROM student_attendance_events WHERE student_id=? AND session_id=? AND event_type=? AND group_event_id=?", (student_id, row["session_id"], row["event_type"], row["group_event_id"])).fetchone()
        if duplicate:
            raise FaceAttendanceError(409, "ATTENDANCE_ALREADY_RECORDED", "This attendance event already exists; no duplicate was written.")
        if row["event_type"] == "CheckOut":
            previous = conn.execute("SELECT attendance_id FROM student_attendance_events WHERE student_id=? AND session_id=? AND event_type='CheckIn' AND group_event_id=?", (student_id, row["session_id"], row["group_event_id"])).fetchone()
            if previous is None:
                raise FaceAttendanceError(409, "CHECKIN_REQUIRED", "Checkout is blocked until a verified check-in exists for the same attendance session/group event.")
        return row, student_id, operator.strip()

    def confirm_student(self, payload: object) -> dict:
        self._assert_ready()
        if not isinstance(payload, dict) or payload.get("confirmMatch") is not True:
            raise FaceAttendanceError(400, "HUMAN_CONFIRMATION_REQUIRED", "Set confirmMatch=true after an authorised staff member checks the candidate.")
        ticket_id = payload.get("ticketId")
        student_id = payload.get("studentId")
        operator = payload.get("operator")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row, student_id, operator = self._confirm_in_tx(conn, ticket_id, student_id, operator, "student", payload.get("sessionId"), None)
                attendance_id = "ATT-" + secrets.token_hex(12).upper()
                now = _iso_utc()
                identity_link = conn.execute("SELECT genetec_cardholder_guid,genetec_credential_refs_json FROM face_enrollments WHERE student_id=?", (student_id,)).fetchone()
                conn.execute("INSERT INTO student_attendance_events(attendance_id,student_id,session_id,event_type,group_event_id,occurred_at_utc,operator_name,ticket_id,model_distance,genetec_cardholder_guid,genetec_credential_refs_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (attendance_id, student_id, row["session_id"], row["event_type"], row["group_event_id"], now, operator, ticket_id, row["distance"], identity_link["genetec_cardholder_guid"], identity_link["genetec_credential_refs_json"]))
                conn.execute("UPDATE face_match_tickets SET consumed_at_utc=? WHERE ticket_id=?", (now, ticket_id))
                self._audit(conn, "StudentAttendanceRecorded", student_id, row["session_id"], "Success", {"eventType": row["event_type"], "groupEventId": row["group_event_id"], "operator": operator, "ticketId": ticket_id, "modelDistance": round(float(row["distance"]), 3)})
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"ok": True, "attendanceId": attendance_id, "studentId": student_id, "sessionId": row["session_id"], "groupEventId": row["group_event_id"] or None, "eventType": row["event_type"], "occurredAtUtc": now, "operator": operator, "humanConfirmed": True}

    def confirm_group_batch(self, payload: object) -> dict:
        self._assert_ready(group=True)
        if not isinstance(payload, dict) or payload.get("confirmBatch") is not True:
            raise FaceAttendanceError(400, "HUMAN_CONFIRMATION_REQUIRED", "Set confirmBatch=true after reviewing each candidate and the roster.")
        group_event_id = _validate_session(payload.get("groupEventId"))
        session_id = _validate_session(payload.get("sessionId", group_event_id))
        operator = payload.get("operator")
        items = payload.get("confirmations")
        if not isinstance(items, list) or not items or len(items) > MAX_GROUP_FACES:
            raise FaceAttendanceError(400, "INVALID_CONFIRMATION_BATCH", f"confirmations must contain 1–{MAX_GROUP_FACES} confirmed candidate tickets.")
        if any(not isinstance(item, dict) or item.get("confirmMatch") is not True for item in items):
            raise FaceAttendanceError(400, "HUMAN_CONFIRMATION_REQUIRED", "Every batch item must explicitly set confirmMatch=true after individual review.")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                checked = []
                tickets = set()
                students = set()
                for item in items:
                    ticket_id = item.get("ticketId")
                    student_id = _validate_id(item.get("studentId"))
                    if ticket_id in tickets or student_id in students:
                        raise FaceAttendanceError(400, "DUPLICATE_BATCH_ITEM", "A batch cannot confirm the same ticket or student more than once.")
                    tickets.add(ticket_id)
                    students.add(student_id)
                    checked.append(self._confirm_in_tx(conn, ticket_id, student_id, operator, "group", session_id, group_event_id))
                now = _iso_utc()
                result_rows = []
                for row, student_id, actor in checked:
                    ticket_id = row["ticket_id"]
                    attendance_id = "ATT-" + secrets.token_hex(12).upper()
                    identity_link = conn.execute("SELECT genetec_cardholder_guid,genetec_credential_refs_json FROM face_enrollments WHERE student_id=?", (student_id,)).fetchone()
                    conn.execute("INSERT INTO student_attendance_events(attendance_id,student_id,session_id,event_type,group_event_id,occurred_at_utc,operator_name,ticket_id,model_distance,genetec_cardholder_guid,genetec_credential_refs_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (attendance_id, student_id, session_id, row["event_type"], group_event_id, now, actor, ticket_id, row["distance"], identity_link["genetec_cardholder_guid"], identity_link["genetec_credential_refs_json"]))
                    conn.execute("UPDATE face_match_tickets SET consumed_at_utc=? WHERE ticket_id=?", (now, ticket_id))
                    self._audit(conn, "GroupAttendanceRecorded", student_id, session_id, "Success", {"eventType": row["event_type"], "groupEventId": group_event_id, "operator": actor, "ticketId": ticket_id, "modelDistance": round(float(row["distance"]), 3)})
                    result_rows.append({"attendanceId": attendance_id, "studentId": student_id, "eventType": row["event_type"], "occurredAtUtc": now, "operator": actor, "humanConfirmed": True})
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"ok": True, "groupEventId": group_event_id, "sessionId": session_id, "recordedCount": len(result_rows), "events": result_rows, "humanConfirmed": True, "groupImagePersisted": False}

    def list_events(self, session_id: object, group_event_id: object = "") -> dict:
        self._assert_ready()
        session_id = _validate_session(session_id)
        if group_event_id not in (None, ""):
            group_event_id = _validate_session(group_event_id)
        else:
            group_event_id = ""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT e.attendance_id,e.student_id,p.display_name,p.class_name,e.session_id,e.event_type,e.group_event_id,e.occurred_at_utc,e.operator_name,e.genetec_cardholder_guid,e.genetec_credential_refs_json FROM student_attendance_events e JOIN face_enrollments p USING(student_id) WHERE e.session_id=? AND e.group_event_id=? ORDER BY e.occurred_at_utc", (session_id, group_event_id)).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            try: item["genetecCredentialReferences"] = _safe_genetec_credential_refs(json.loads(item.pop("genetec_credential_refs_json") or "[]"))
            except Exception: item["genetecCredentialReferences"] = []
            events.append(item)
        return {"ok": True, "sessionId": session_id, "groupEventId": group_event_id or None, "events": events}

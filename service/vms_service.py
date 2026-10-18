#!/usr/bin/env python3
"""Loopback-only local persistence/static service for the Stamford VMS prototype.

This service is deliberately a prototype persistence layer, not a production API:
there is no enterprise authentication/authorization and browser snapshots remain
client-authored. It binds only to loopback and uses optimistic revision checks to
prevent silent last-write-wins overwrites between browser sessions.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs

try:
    from .qr_frame_decoder import decode_qr_data_url, QRDecoderUnavailable
except ImportError:  # direct script execution
    from qr_frame_decoder import decode_qr_data_url, QRDecoderUnavailable

try:
    from .domain import DomainError, DomainStore
except ImportError:  # direct script execution
    from domain import DomainError, DomainStore

try:
    from .face_attendance import FaceAttendanceError, FaceAttendanceStore
except ImportError:  # direct script execution
    from face_attendance import FaceAttendanceError, FaceAttendanceStore

try:
    from .integrations import IntegrationError, IntegrationHub
except ImportError:  # direct script execution
    from integrations import IntegrationError, IntegrationHub

MAX_BODY_BYTES = 4 * 1024 * 1024
SCHEMA_VERSION = 1
STATE_LIMITS = {
    "visits": 5000,
    "cards": 5000,
    "groups": 2000,
    "events": 10000,
    "audit": 200,
    "activity": 8,
}
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def default_data_dir() -> Path:
    override = os.environ.get("STAMFORD_VMS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "StamfordVMS" / "VmsPrototype"
    return Path.home() / ".local" / "share" / "stamford-vms-prototype"


class StateStore:
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
            conn.execute("""CREATE TABLE IF NOT EXISTS app_state (
                state_key TEXT PRIMARY KEY CHECK(state_key = 'prototype'),
                revision INTEGER NOT NULL CHECK(revision >= 1),
                saved_at_utc TEXT NOT NULL,
                state_json TEXT NOT NULL
            )""")

    def load(self) -> dict:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT revision, saved_at_utc, state_json FROM app_state WHERE state_key='prototype'").fetchone()
        if row is None:
            return {"service": "Stamford VMS local prototype service", "mode": "prototype-only", "persistence": "sqlite-local", "revision": 0, "hasState": False, "state": None}
        try:
            state = json.loads(row["state_json"])
        except json.JSONDecodeError as exc:
            raise RuntimeError("Persisted prototype state is corrupt; stop and preserve the database for inspection.") from exc
        return {"service": "Stamford VMS local prototype service", "mode": "prototype-only", "persistence": "sqlite-local", "revision": row["revision"], "savedAtUtc": row["saved_at_utc"], "hasState": True, "state": state}

    def save(self, expected_revision: int, state: dict) -> dict:
        import datetime
        saved_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT revision FROM app_state WHERE state_key='prototype'").fetchone()
            current_revision = int(row["revision"]) if row else 0
            if current_revision != expected_revision:
                conn.execute("ROLLBACK")
                return {"ok": False, "conflict": True, "expectedRevision": expected_revision, "serverRevision": current_revision}
            next_revision = current_revision + 1
            if row:
                conn.execute("UPDATE app_state SET revision=?, saved_at_utc=?, state_json=? WHERE state_key='prototype'", (next_revision, saved_at, payload))
            else:
                conn.execute("INSERT INTO app_state(state_key,revision,saved_at_utc,state_json) VALUES('prototype',?,?,?)", (next_revision, saved_at, payload))
            conn.execute("COMMIT")
        return {"ok": True, "revision": next_revision, "savedAtUtc": saved_at, "persistence": "sqlite-local", "mode": "prototype-only"}


def validate_snapshot(payload: object) -> tuple[int, dict]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    revision = payload.get("expectedRevision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("expectedRevision must be a non-negative integer.")
    state = payload.get("state")
    if not isinstance(state, dict) or state.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("state must be a schemaVersion=1 object.")
    normalized = {"schemaVersion": SCHEMA_VERSION}
    for key, limit in STATE_LIMITS.items():
        value = state.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"state.{key} must be an array.")
        if len(value) > limit:
            raise ValueError(f"state.{key} exceeds the prototype limit of {limit} items.")
        if not all(isinstance(item, dict) for item in value):
            raise ValueError(f"every state.{key} item must be an object.")
        normalized[key] = value
    qr_index = state.get("nextQrIndex", 19)
    if isinstance(qr_index, bool) or not isinstance(qr_index, int) or qr_index < 0 or qr_index > 10_000_000:
        raise ValueError("state.nextQrIndex must be a non-negative bounded integer.")
    normalized["nextQrIndex"] = qr_index
    saved = state.get("savedAtUtc")
    if isinstance(saved, str) and len(saved) <= 64:
        normalized["savedAtUtc"] = saved
    return revision, normalized


def make_handler(ui_dir: Path, store: StateStore, domain_store: DomainStore | None = None, face_store: FaceAttendanceStore | None = None, integration_hub: IntegrationHub | None = None):
    ui_dir = ui_dir.resolve()
    domain_store = domain_store or DomainStore(store.db_path)
    face_store = face_store or FaceAttendanceStore(store.db_path.with_name("face-attendance.sqlite3"))
    integration_hub = integration_hub or IntegrationHub(store.db_path)

    class Handler(SimpleHTTPRequestHandler):
        server_version = "StamfordVMSPrototype/5.2.4"
        sys_version = ""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ui_dir), **kwargs)

        def log_message(self, fmt, *args):
            # Log method/path only. Never log query strings, request bodies, visitor fields, or tokens.
            logging.info("%s %s %s", self.client_address[0], self.command, urlsplit(self.path).path[:120])

        def _send_json(self, code: int, payload: dict):
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _send_svg(self, code: int, payload: bytes):
            self.send_response(code)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Disposition", "inline; filename=visitor-pass.svg")
            self.end_headers()
            self.wfile.write(payload)

        def _read_json_body(self):
            raw_len = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_len)
            except ValueError:
                raise ValueError("INVALID_CONTENT_LENGTH")
            if length <= 0 or length > MAX_BODY_BYTES:
                raise OverflowError("BODY_SIZE_INVALID")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("INVALID_JSON") from exc

        def _send_domain_error(self, exc: DomainError):
            payload = {"ok": False, "error": exc.code, "message": exc.message}
            payload.update(exc.extra)
            self._send_json(exc.status, payload)

        def _send_face_error(self, exc: FaceAttendanceError):
            payload = {"ok": False, "error": exc.code, "message": exc.message}
            payload.update(exc.extra)
            self._send_json(exc.status, payload)

        def _request_allowed(self) -> bool:
            host_header = self.headers.get("Host", "")
            parsed_host = urlsplit("//" + host_header)
            hostname = (parsed_host.hostname or "").lower()
            if hostname not in ALLOWED_HOSTS:
                self._send_json(403, {"ok": False, "error": "LOOPBACK_HOST_REQUIRED", "message": "This prototype service only accepts loopback Host headers."})
                return False
            origin = self.headers.get("Origin")
            if origin:
                origin_parts = urlsplit(origin)
                origin_host = (origin_parts.hostname or "").lower()
                try:
                    origin_port = origin_parts.port or (443 if origin_parts.scheme == "https" else 80)
                    host_port = parsed_host.port or (443 if self.request_version == "HTTPS/1.1" else 80)
                except ValueError:
                    origin_port, host_port = -1, -2
                same_origin = origin_parts.scheme == "http" and origin_host in ALLOWED_HOSTS and origin_port == host_port
                if not same_origin:
                    self._send_json(403, {"ok": False, "error": "CROSS_ORIGIN_REJECTED", "message": "Cross-origin browser requests are not enabled."})
                    return False
            return True

        def do_OPTIONS(self):
            if not self._request_allowed():
                return
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self):
            if not self._request_allowed():
                return
            path = urlsplit(self.path).path
            if path == "/api/v1/health":
                self._send_json(200, {"ok": True, "service": "Stamford VMS local prototype service", "build": "5.2.4", "schoolTimeZone": "Asia/Singapore", "mode": "prototype-only", "persistence": "sqlite-local", "authentication": "not-configured", "boundToLoopback": True})
                return
            if path == "/api/v1/integrations/status":
                try:
                    self._send_json(200, integration_hub.status())
                except Exception:
                    logging.exception("Unable to read adapter status")
                    self._send_json(500, {"ok": False, "error": "INTEGRATION_STATUS_FAILED", "message": "Integration status could not be read."})
                return
            if path == "/api/v1/integrations/sms/outbox":
                try:
                    self._send_json(200, {"ok": True, "messages": integration_hub.list_outbox()})
                except Exception:
                    logging.exception("Unable to read SMS outbox")
                    self._send_json(500, {"ok": False, "error": "SMS_OUTBOX_FAILED", "message": "The local SMS outbox could not be read."})
                return
            if path == "/api/v1/state":
                try:
                    self._send_json(200, store.load())
                except Exception:
                    logging.exception("Unable to load prototype state")
                    self._send_json(500, {"ok": False, "error": "STATE_LOAD_FAILED", "message": "Saved state could not be loaded. Stop and preserve the local database for investigation."})
                return
            if path == "/api/v1/visits":
                try:
                    self._send_json(200, {"ok": True, "visits": domain_store.list_visits(), "source": "transactional-local-domain-store", "authentication": "not-configured"})
                except Exception:
                    logging.exception("Unable to list domain visits")
                    self._send_json(500, {"ok": False, "error": "VISIT_LIST_FAILED", "message": "Domain visit records could not be loaded."})
                return
            match = re.fullmatch(r"/api/v1/visits/([^/]+)", path)
            if match:
                try:
                    result = domain_store.get_visit(unquote(match.group(1)))
                    if result is None:
                        self._send_json(404, {"ok": False, "error": "VISIT_NOT_FOUND", "message": "No visit exists for that booking ID."})
                    else:
                        self._send_json(200, {"ok": True, "visit": result})
                except Exception:
                    logging.exception("Unable to read domain visit")
                    self._send_json(500, {"ok": False, "error": "VISIT_READ_FAILED", "message": "Domain visit could not be loaded."})
                return
            if path == "/api/v1/face-attendance/status":
                try:
                    self._send_json(200, face_store.status())
                except Exception:
                    logging.exception("Unable to inspect face-attendance feature status")
                    self._send_json(500, {"ok": False, "error": "FACE_STATUS_FAILED", "message": "Face-attendance status could not be read."})
                return
            if path == "/api/v1/kiosk-face/status":
                try:
                    self._send_json(200, face_store.kiosk_status())
                except Exception:
                    logging.exception("Unable to inspect kiosk face adapter status")
                    self._send_json(500, {"ok": False, "error": "KIOSK_FACE_STATUS_FAILED", "message": "Kiosk face status could not be read."})
                return
            if path == "/api/v1/kiosk-face/events":
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    session_id = (query.get("sessionId") or [""])[0]
                    self._send_json(200, face_store.list_kiosk_events(session_id))
                except FaceAttendanceError as exc:
                    self._send_face_error(exc)
                except Exception:
                    logging.exception("Unable to list kiosk attendance events")
                    self._send_json(500, {"ok": False, "error": "KIOSK_FACE_EVENTS_FAILED", "message": "Kiosk attendance events could not be loaded."})
                return
            if path == "/api/v1/face-attendance/students":
                try:
                    self._send_json(200, face_store.list_students())
                except FaceAttendanceError as exc:
                    self._send_face_error(exc)
                except Exception:
                    logging.exception("Unable to list consented face enrollments")
                    self._send_json(500, {"ok": False, "error": "FACE_STUDENT_LIST_FAILED", "message": "The enrolled-student list could not be loaded."})
                return
            if path == "/api/v1/genetec/student-roster/groups":
                try:
                    self._send_json(200, face_store.list_genetec_student_groups())
                except FaceAttendanceError as exc:
                    self._send_face_error(exc)
                except Exception:
                    logging.exception("Unable to load Genetec Cardholder groups")
                    self._send_json(500, {"ok": False, "error": "GENETEC_GROUPS_FAILED", "message": "Genetec Cardholder groups could not be loaded."})
                return
            if path == "/api/v1/genetec/student-roster":
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    search = (query.get("search") or [""])[0]
                    self._send_json(200, face_store.list_genetec_student_roster(search))
                except FaceAttendanceError as exc:
                    self._send_face_error(exc)
                except Exception:
                    logging.exception("Unable to list imported Genetec student roster")
                    self._send_json(500, {"ok": False, "error": "GENETEC_ROSTER_LIST_FAILED", "message": "The imported student roster could not be loaded."})
                return
            if path == "/api/v1/genetec/student-roster/attendance":
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    session_id = (query.get("sessionId") or [""])[0]
                    self._send_json(200, face_store.list_genetec_roster_attendance(session_id))
                except FaceAttendanceError as exc:
                    self._send_face_error(exc)
                except Exception:
                    logging.exception("Unable to list Genetec roster attendance")
                    self._send_json(500, {"ok": False, "error": "GENETEC_ROSTER_ATTENDANCE_LIST_FAILED", "message": "The roster attendance events could not be loaded."})
                return
            portrait_match = re.fullmatch(r"/api/v1/genetec/student-roster/([0-9a-fA-F-]{36})/portrait", path)
            if portrait_match:
                try:
                    self._send_json(200, face_store.get_genetec_roster_portrait(portrait_match.group(1)))
                except FaceAttendanceError as exc:
                    self._send_face_error(exc)
                except Exception:
                    logging.exception("Unable to load Genetec portrait for staff review")
                    self._send_json(500, {"ok": False, "error": "GENETEC_ROSTER_PORTRAIT_FAILED", "message": "The selected portrait could not be loaded."})
                return
            if path == "/api/v1/face-attendance/genetec-cardholders":
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    search = (query.get("search") or [None])[0]
                    self._send_json(200, face_store.list_genetec_cardholders(search))
                except FaceAttendanceError as exc:
                    self._send_face_error(exc)
                except Exception:
                    logging.exception("Unable to search Genetec Cardholders for approved biometric import")
                    self._send_json(500, {"ok": False, "error": "GENETEC_CARDHOLDER_SEARCH_FAILED", "message": "Genetec Cardholder search failed; inspect the local bridge status."})
                return
            if path == "/api/v1/face-attendance/events":
                query = parse_qs(urlsplit(self.path).query)
                try:
                    session_id = (query.get("sessionId") or [None])[0]
                    group_event_id = (query.get("groupEventId") or [""])[0]
                    self._send_json(200, face_store.list_events(session_id, group_event_id))
                except FaceAttendanceError as exc:
                    self._send_face_error(exc)
                except Exception:
                    logging.exception("Unable to list face-attendance events")
                    self._send_json(500, {"ok": False, "error": "FACE_EVENTS_FAILED", "message": "Attendance events could not be loaded."})
                return
            # The built-in static server only serves files below ui_dir.
            return super().do_GET()

        def do_PUT(self):
            if not self._request_allowed():
                return
            if urlsplit(self.path).path != "/api/v1/state":
                self._send_json(404, {"ok": False, "error": "NOT_FOUND"})
                return
            raw_len = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_len)
            except ValueError:
                self._send_json(400, {"ok": False, "error": "INVALID_CONTENT_LENGTH"})
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send_json(413 if length > MAX_BODY_BYTES else 400, {"ok": False, "error": "BODY_SIZE_INVALID", "maxBytes": MAX_BODY_BYTES})
                return
            try:
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                expected_revision, state = validate_snapshot(payload)
                result = store.save(expected_revision, state)
                if result.get("conflict"):
                    self._send_json(409, {"ok": False, "error": "STATE_REVISION_CONFLICT", **result, "message": "Another browser session saved newer state. Autosave has been stopped to avoid overwriting it; reload and review before continuing."})
                    return
                self._send_json(200, result)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._send_json(400, {"ok": False, "error": "INVALID_STATE", "message": str(exc)[:240]})
            except Exception:
                logging.exception("Unable to save prototype state")
                self._send_json(500, {"ok": False, "error": "STATE_SAVE_FAILED", "message": "The local service could not commit the state snapshot."})


        def do_POST(self):
            if not self._request_allowed():
                return
            path = unquote(urlsplit(self.path).path)
            try:
                payload = self._read_json_body()
            except OverflowError as exc:
                self._send_json(413, {"ok": False, "error": str(exc), "maxBytes": MAX_BODY_BYTES})
                return
            except ValueError as exc:
                code = str(exc)
                self._send_json(400, {"ok": False, "error": code, "message": "Request body must be valid UTF-8 JSON and within the configured size limit."})
                return

            try:
                if path == "/api/v1/integrations/sms/send":
                    result = integration_hub.send_sms(payload)
                    self._send_json(200 if result.get("alreadyApplied") else 201, result)
                    return
                if path == "/api/v1/visits":
                    result = domain_store.create_visit(payload)
                    self._send_json(201, result)
                    return
                match = re.fullmatch(r"/api/v1/visits/([^/]+)/amend", path)
                if match:
                    self._send_json(200, domain_store.amend_visit_schedule(match.group(1), payload))
                    return
                match = re.fullmatch(r"/api/v1/visits/([^/]+)/qr/rotate", path)
                if match:
                    self._send_json(200, domain_store.rotate_qr(match.group(1)))
                    return
                match = re.fullmatch(r"/api/v1/visits/([^/]+)/cancel", path)
                if match:
                    self._send_json(200, domain_store.cancel_visit(match.group(1)))
                    return
                if path == "/api/v1/qr/decode-frame":
                    image_data = payload.get("imageData") if isinstance(payload, dict) else None
                    try:
                        decoded = decode_qr_data_url(image_data)
                    except QRDecoderUnavailable:
                        self._send_json(503, {"ok": False, "error": "QR_DECODER_NOT_INSTALLED", "message": "The local QR camera decoder is not installed. Restart Start-Demo.cmd while this computer has internet access, or run python -m pip install --user -r requirements-scanner.txt once."})
                        return
                    except ValueError as exc:
                        self._send_json(400, {"ok": False, "error": str(exc), "message": "The camera frame was invalid or exceeded the local scanner limits."})
                        return
                    self._send_json(200, {"ok": True, "detected": bool(decoded), "qrText": decoded or None})
                    return
                if path == "/api/v1/qr/validate":
                    self._send_json(200, domain_store.validate_qr(payload.get("qrToken") if isinstance(payload, dict) else None))
                    return
                if path == "/api/v1/qr/svg":
                    token = payload.get("qrToken") if isinstance(payload, dict) else None
                    self._send_svg(200, domain_store.qr_svg(token))
                    return
                if path in ("/api/v1/qr/check-in", "/api/v1/qr/check-out"):
                    action = "CheckIn" if path.endswith("check-in") else "CheckOut"
                    token = payload.get("qrToken") if isinstance(payload, dict) else None
                    event_id = payload.get("eventId") if isinstance(payload, dict) else None
                    self._send_json(200, domain_store.transition_by_qr(token, action, event_id))
                    return
                if path == "/api/v1/genetec/student-roster/import":
                    self._send_json(200, face_store.import_genetec_student_roster(payload))
                    return
                attendance_match = re.fullmatch(r"/api/v1/genetec/student-roster/([0-9a-fA-F-]{36})/attendance", path)
                if attendance_match:
                    self._send_json(201, face_store.record_genetec_roster_attendance(attendance_match.group(1), payload))
                    return
                if path == "/api/v1/face-attendance/enroll":
                    self._send_json(201, face_store.enroll(payload))
                    return
                if path == "/api/v1/face-attendance/enroll-from-genetec":
                    self._send_json(201, face_store.enroll_from_genetec(payload))
                    return
                match = re.fullmatch(r"/api/v1/face-attendance/students/([^/]+)/revoke", path)
                if match:
                    self._send_json(200, face_store.revoke_enrollment(unquote(match.group(1)), payload))
                    return
                if path == "/api/v1/face-attendance/match":
                    self._send_json(200, face_store.match_student(payload))
                    return
                if path == "/api/v1/face-attendance/confirm":
                    self._send_json(200, face_store.confirm_student(payload))
                    return
                if path == "/api/v1/kiosk-face/match":
                    self._send_json(200, face_store.match_kiosk_face(payload))
                    return
                if path == "/api/v1/kiosk-face/confirm":
                    self._send_json(201, face_store.confirm_kiosk_face(payload))
                    return
                if path == "/api/v1/face-attendance/group-match":
                    self._send_json(200, face_store.match_group(payload))
                    return
                if path == "/api/v1/face-attendance/group-confirm-batch":
                    self._send_json(200, face_store.confirm_group_batch(payload))
                    return
                self._send_json(404, {"ok": False, "error": "NOT_FOUND", "message": "No API route matches this request."})
            except DomainError as exc:
                self._send_domain_error(exc)
            except FaceAttendanceError as exc:
                self._send_face_error(exc)
            except IntegrationError as exc:
                payload = {"ok": False, "error": exc.code, "message": exc.message}
                payload.update(exc.extra)
                self._send_json(exc.status, payload)
            except Exception:
                # Never log request bodies; they can contain raw QR tokens.
                logging.exception("Domain API request failed for route %s", path[:100])
                self._send_json(500, {"ok": False, "error": "DOMAIN_OPERATION_FAILED", "message": "The domain operation failed. Inspect the local service log; request payloads are not logged."})

        def end_headers(self):
            # Local prototype updates must not be hidden by browser/proxy caches.
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui-dir", type=Path, default=Path(__file__).resolve().parents[1] / "ui")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"], help="Loopback only; binding to LAN/Internet is intentionally unsupported.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args(argv)
    if not args.ui_dir.is_dir():
        parser.error(f"UI directory does not exist: {args.ui_dir}")
    if not (1 <= args.port <= 65535):
        parser.error("port must be between 1 and 65535")
    try:
        database_path = args.data_dir / "prototype-state.sqlite3"
        store = StateStore(database_path)
        domain_store = DomainStore(database_path)
        server = ThreadingHTTPServer((args.host, args.port), make_handler(args.ui_dir, store, domain_store))
        server.daemon_threads = True
        logging.info("Serving Stamford VMS prototype at http://%s:%s", args.host, args.port)
        logging.info("Local SQLite database: %s", args.data_dir / "prototype-state.sqlite3")
        logging.info("Domain API routes: POST /api/v1/visits, POST /api/v1/qr/decode-frame, POST /api/v1/qr/validate, POST /api/v1/qr/check-in, POST /api/v1/qr/check-out, POST /api/v1/qr/svg")
        logging.info("Prototype adapters: GET /api/v1/integrations/status; GET /api/v1/integrations/sms/outbox; POST /api/v1/integrations/sms/send (mock by default; live provider is allow-list gated)")
        logging.info("Optional gated face attendance API: GET /api/v1/face-attendance/status; POST /api/v1/face-attendance/enroll|match|confirm|group-match|group-confirm-batch")
        logging.info("Approved local Genetec student roster API: GET /api/v1/genetec/student-roster/groups; POST /api/v1/genetec/student-roster/import; GET /api/v1/genetec/student-roster; GET /api/v1/genetec/student-roster/{guid}/portrait; POST /api/v1/genetec/student-roster/{guid}/attendance")
        logging.warning("PROTOTYPE ONLY: no enterprise authentication/authorization; use synthetic data only; do not expose beyond loopback")
        logging.info("Press Ctrl+C to stop")
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            logging.info("Stopping local prototype service")
        finally:
            server.shutdown()
            server.server_close()
        return 0
    except OSError as exc:
        logging.error("Unable to start service: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())

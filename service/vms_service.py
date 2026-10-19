#!/usr/bin/env python3
"""Loopback-only local persistence/static service for the Stamford VMS prototype.

This service is a loopback-only prototype API. Local account authentication and
server-enforced feature permissions are enabled by default. Browser snapshots remain
client-authored demo state, while transactional endpoints have explicit permission gates.
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

try:
    from .group_visits import GroupVisitError, GroupVisitStore
except ImportError:  # direct script execution
    from group_visits import GroupVisitError, GroupVisitStore

try:
    from .reporting import ReportingError, ReportService
except ImportError:  # direct script execution
    from reporting import ReportingError, ReportService

try:
    from .access_control import AccessControlError, AccessControlStore, PERMISSIONS
except ImportError:  # direct script execution
    from access_control import AccessControlError, AccessControlStore, PERMISSIONS

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


def required_permissions(method: str, path: str, query: str = "") -> list[str] | None:
    """Closed-by-default server-side route to feature-permission mapping."""
    method = method.upper()
    if path == "/api/v1/health": return []
    if method == "GET":
        if path == "/api/v1/state": return ["snapshot.view"]
        if path in {"/api/v1/access/accounts"}: return ["accounts.manage"]
        if path in {"/api/v1/access/groups"}: return ["permissions.manage"]
        if path == "/api/v1/access/permission-catalog": return ["permissions.manage"]
        if path == "/api/v1/access/audit": return ["permissions.manage"]
        if path == "/api/v1/integrations/status": return ["integrations.view"]
        if path == "/api/v1/integrations/sms/outbox": return ["integrations.view"]
        if path == "/api/v1/reports/catalog": return ["reports.view"]
        if path == "/api/v1/visitor-types": return ["visitor_types.view"]
        if path == "/api/v1/visits" or re.fullmatch(r"/api/v1/visits/[^/]+", path): return ["visits.view"]
        if path in {"/api/v1/group-visits", "/api/v1/group-visits/status"} or re.fullmatch(r"/api/v1/group-visits/[^/]+", path): return ["groups.view"]
        if path in {"/api/v1/face-attendance/status", "/api/v1/face-attendance/students", "/api/v1/face-attendance/genetec-cardholders", "/api/v1/face-attendance/events", "/api/v1/kiosk-face/status", "/api/v1/kiosk-face/events", "/api/v1/genetec/student-roster", "/api/v1/genetec/student-roster/groups", "/api/v1/genetec/student-roster/attendance"}: return ["attendance.view"] if "genetec/student-roster" not in path else ["genetec.view"]
        if re.fullmatch(r"/api/v1/genetec/student-roster/[0-9a-fA-F-]{36}/portrait", path): return ["genetec.view"]
        if path.startswith("/api/v1/"): return None
        return None
    if method == "PUT":
        if path == "/api/v1/state": return ["snapshot.manage"]
        return None
    if method == "POST":
        if path == "/api/v1/state": return ["snapshot.manage"]
        if path in {"/api/v1/reports/preview"}: return ["reports.view"]
        if path == "/api/v1/reports/export": return ["reports.export"]
        if path == "/api/v1/integrations/sms/send": return ["integrations.manage"]
        if path == "/api/v1/visitor-types": return ["visitor_types.manage"]
        if re.fullmatch(r"/api/v1/visitor-types/[a-z0-9-]+/update", path, re.I): return ["visitor_types.manage"]
        if path in {"/api/v1/visits"}: return ["visits.create"]
        if re.fullmatch(r"/api/v1/visits/[^/]+/(amend|cancel|qr/rotate)", path): return ["visits.manage"]
        if path in {"/api/v1/qr/decode-frame", "/api/v1/qr/validate", "/api/v1/qr/svg", "/api/v1/qr/check-in", "/api/v1/qr/check-out"}: return ["visits.kiosk"]
        if path in {"/api/v1/group-visits", "/api/v1/group-visits/qr/validate", "/api/v1/group-visits/qr/svg"}: return ["groups.manage"] if path == "/api/v1/group-visits" else ["groups.view"]
        if re.fullmatch(r"/api/v1/group-visits/[^/]+/(qr/rotate|roster|scan|confirm|reconcile)", path): return ["groups.manage"]
        if path == "/api/v1/genetec/student-roster/import": return ["genetec.manage"]
        if re.fullmatch(r"/api/v1/genetec/student-roster/[0-9a-fA-F-]{36}/attendance", path): return ["attendance.manage"]
        if path == "/api/v1/face-attendance/enroll-from-genetec": return ["attendance.manage", "genetec.manage"]
        if path in {"/api/v1/face-attendance/enroll", "/api/v1/face-attendance/confirm", "/api/v1/face-attendance/group-confirm-batch", "/api/v1/kiosk-face/confirm"} or re.fullmatch(r"/api/v1/face-attendance/students/[^/]+/revoke", path): return ["attendance.manage"]
        if path == "/api/v1/face-attendance/genetec-sync/retry": return ["genetec.manage"]
        if path in {"/api/v1/face-attendance/match", "/api/v1/face-attendance/group-match", "/api/v1/kiosk-face/match"}: return ["attendance.view"]
        if path == "/api/v1/access/accounts": return ["accounts.manage", "permissions.manage"]
        if re.fullmatch(r"/api/v1/access/accounts/[0-9]+/(update|reset-password)", path): return ["accounts.manage"]
        if re.fullmatch(r"/api/v1/access/accounts/[0-9]+/(groups|overrides)", path): return ["permissions.manage"]
        if path == "/api/v1/access/groups" or re.fullmatch(r"/api/v1/access/groups/[^/]+/(permissions|update)", path): return ["permissions.manage"]
        if path.startswith("/api/v1/"): return None
        return None
    return None


def make_handler(ui_dir: Path, store: StateStore, domain_store: DomainStore | None = None, face_store: FaceAttendanceStore | None = None, integration_hub: IntegrationHub | None = None, group_store: GroupVisitStore | None = None, access_store: AccessControlStore | None = None, auth_enabled: bool = True):
    ui_dir = ui_dir.resolve()
    domain_store = domain_store or DomainStore(store.db_path)
    face_store = face_store or FaceAttendanceStore(store.db_path.with_name("face-attendance.sqlite3"))
    group_store = group_store or GroupVisitStore(face_store.db_path)
    integration_hub = integration_hub or IntegrationHub(store.db_path)
    access_store = access_store or AccessControlStore(store.db_path.with_name("access-control.sqlite3"))
    report_service = ReportService(store.db_path, face_store.db_path)

    class Handler(SimpleHTTPRequestHandler):
        server_version = "StamfordVMSPrototype/5.5.0"
        sys_version = ""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ui_dir), **kwargs)

        def log_message(self, fmt, *args):
            # Log method/path only. Never log query strings, request bodies, visitor fields, or tokens.
            logging.info("%s %s %s", self.client_address[0], self.command, urlsplit(self.path).path[:120])

        def _send_json(self, code: int, payload: dict, headers: dict | None = None):
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            for header_name, header_value in (headers or {}).items():
                self.send_header(header_name, header_value)
            self.end_headers()
            self.wfile.write(raw)

        def _send_download(self, code: int, content_type: str, filename: str, payload: bytes):
            # Only generated, server-controlled filenames are passed here.
            safe_filename = re.sub(r"[^A-Za-z0-9._-]", "-", filename)[:180] or "stamford-vms-report.bin"
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Disposition", f'attachment; filename="{safe_filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def _send_svg(self, code: int, payload: bytes):
            self.send_response(code)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Disposition", "inline; filename=visitor-pass.svg")
            self.end_headers()
            self.wfile.write(payload)

        def _session_token(self) -> str | None:
            from http.cookies import SimpleCookie
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                return cookie["stamford_vms_session"].value if "stamford_vms_session" in cookie else None
            except Exception:
                return None

        def _auth_context(self):
            if not auth_enabled:
                return {"user": {"accountId": 0, "username": "test-bypass", "displayName": "Test bypass", "isActive": True}, "groups": [], "permissions": sorted(PERMISSIONS and {p["key"] for p in PERMISSIONS}), "csrfToken": "test-csrf", "sessionHash": "test"}
            if not hasattr(self, "_resolved_auth_context"):
                self._resolved_auth_context = access_store.resolve_session(self._session_token())
            return self._resolved_auth_context

        def _clear_session_cookie(self):
            return "stamford_vms_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"

        def _session_cookie(self, token: str):
            return f"stamford_vms_session={token}; Max-Age={8 * 60 * 60}; Path=/; HttpOnly; SameSite=Strict"

        def _handle_auth_status(self):
            context = self._auth_context()
            self._send_json(200, {"ok": True, "setupRequired": access_store.account_count() == 0, "authenticated": bool(context),
                                  "user": context.get("user") if context else None,
                                  "groups": context.get("groups", []) if context else [],
                                  "permissions": context.get("permissions", []) if context else [],
                                  "csrfToken": context.get("csrfToken") if context else None,
                                  "sessionExpiresInHours": 8 if context else None})

        def _require_permission(self, method: str, path: str) -> bool:
            if not auth_enabled or path == "/api/v1/health":
                return True
            if path.startswith("/api/v1/auth/"):
                return True
            context = self._auth_context()
            if not context:
                self._send_json(401, {"ok": False, "error": "AUTHENTICATION_REQUIRED", "message": "Sign in to Stamford VMS to continue."})
                return False
            if path.startswith("/api/v1/access/") and not any(group.get("groupId") == "administrator" for group in context.get("groups", [])):
                self._send_json(403, {"ok": False, "error": "ADMINISTRATOR_ROLE_REQUIRED", "message": "Only the built-in Administrator role can view or change account and permission administration data."})
                return False
            required = required_permissions(method, path, urlsplit(self.path).query)
            # Group scan routes may run the live face matcher. Mock capture does not need biometric permission.
            if method == "POST" and re.fullmatch(r"/api/v1/group-visits/[^/]+/scan", path) and group_store.status().get("mode") == "live":
                required = (required or []) + ["attendance.view"]
            # Portraits contain biometric imagery; reading the general Genetec roster is not enough.
            if method == "GET" and re.fullmatch(r"/api/v1/genetec/student-roster/[0-9a-fA-F-]{36}/portrait", path):
                required = ["genetec.manage"]
            if required is None:
                self._send_json(403, {"ok": False, "error": "PERMISSION_MAPPING_REQUIRED", "message": "This API operation has no permission mapping and is denied by default."})
                return False
            missing = [permission for permission in required if permission not in context["permissions"]]
            if missing:
                self._send_json(403, {"ok": False, "error": "PERMISSION_DENIED", "message": "Your account is not permitted to perform this operation.", "requiredPermissions": required})
                return False
            if method in {"POST", "PUT", "PATCH", "DELETE"} and path not in {"/api/v1/auth/login", "/api/v1/auth/bootstrap"}:
                if not access_store.validate_csrf(context, self.headers.get("X-Stamford-CSRF")):
                    self._send_json(403, {"ok": False, "error": "CSRF_VALIDATION_FAILED", "message": "Refresh your authenticated session before performing this action."})
                    return False
            self._resolved_auth_context = context
            return True

        def _handle_auth_post(self, path: str, payload: dict) -> bool:
            if path == "/api/v1/auth/bootstrap":
                try:
                    context, token, csrf = access_store.create_bootstrap_admin(payload.get("username"), payload.get("displayName"), payload.get("password"))
                    self._resolved_auth_context = context
                    self._send_json(201, {"ok": True, "user": context["user"], "groups": context["groups"], "permissions": context["permissions"], "csrfToken": csrf}, {"Set-Cookie": self._session_cookie(token)})
                except AccessControlError as exc:
                    self._send_json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})
                except ValueError as exc:
                    self._send_json(400, {"ok": False, "error": "INVALID_BOOTSTRAP", "message": str(exc)})
                return True
            if path == "/api/v1/auth/login":
                try:
                    context, token, csrf = access_store.login(payload.get("username", ""), payload.get("password", ""))
                    self._resolved_auth_context = context
                    self._send_json(200, {"ok": True, "user": context["user"], "groups": context["groups"], "permissions": context["permissions"], "csrfToken": csrf}, {"Set-Cookie": self._session_cookie(token)})
                except AccessControlError as exc:
                    self._send_json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})
                return True
            if path == "/api/v1/auth/logout":
                context = self._auth_context()
                if context and auth_enabled:
                    if not access_store.validate_csrf(context, self.headers.get("X-Stamford-CSRF")):
                        self._send_json(403, {"ok": False, "error": "CSRF_VALIDATION_FAILED", "message": "Refresh your authenticated session before logging out."})
                        return True
                    access_store.logout(context)
                self._resolved_auth_context = None
                self._send_json(200, {"ok": True}, {"Set-Cookie": self._clear_session_cookie()})
                return True
            return False

        def _handle_access_control_post(self, path: str, payload: dict) -> bool:
            context = self._auth_context()
            if path == "/api/v1/access/accounts":
                result = access_store.create_account(payload, context); self._send_json(201, result); return True
            m = re.fullmatch(r"/api/v1/access/accounts/(\d+)/update", path)
            if m:
                self._send_json(200, access_store.update_account(int(m.group(1)), payload, context)); return True
            m = re.fullmatch(r"/api/v1/access/accounts/(\d+)/reset-password", path)
            if m:
                self._send_json(200, access_store.reset_password(int(m.group(1)), payload.get("password"), context)); return True
            m = re.fullmatch(r"/api/v1/access/accounts/(\d+)/groups", path)
            if m:
                self._send_json(200, access_store.assign_groups(int(m.group(1)), payload.get("groupIds"), context)); return True
            m = re.fullmatch(r"/api/v1/access/accounts/(\d+)/overrides", path)
            if m:
                self._send_json(200, access_store.set_overrides(int(m.group(1)), payload.get("overrides"), context)); return True
            if path == "/api/v1/access/groups":
                self._send_json(201, access_store.create_group(payload, context)); return True
            m = re.fullmatch(r"/api/v1/access/groups/([^/]+)/permissions", path)
            if m:
                self._send_json(200, access_store.update_group_permissions(unquote(m.group(1)), payload.get("permissionKeys"), context)); return True
            m = re.fullmatch(r"/api/v1/access/groups/([^/]+)/update", path)
            if m:
                self._send_json(200, access_store.update_group(unquote(m.group(1)), payload, context)); return True
            return False

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

        def _send_group_error(self, exc: GroupVisitError):
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
            if path == "/api/v1/auth/status":
                self._handle_auth_status()
                return
            if path == "/api/v1/health":
                self._send_json(200, {"ok": True, "service": "Stamford VMS local prototype service", "build": "5.5.0", "schoolTimeZone": "Asia/Singapore", "mode": "prototype-only", "persistence": "sqlite-local", "authentication": "enabled" if auth_enabled else "test-bypass", "boundToLoopback": True})
                return
            if path.startswith("/api/v1/") and not self._require_permission("GET", path):
                return
            if path == "/api/v1/access/permission-catalog":
                self._send_json(200, {"ok": True, "permissions": PERMISSIONS})
                return
            if path == "/api/v1/access/accounts":
                self._send_json(200, access_store.list_accounts())
                return
            if path == "/api/v1/access/groups":
                self._send_json(200, access_store.list_groups())
                return
            if path == "/api/v1/access/audit":
                self._send_json(200, access_store.list_audit())
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
            if path == "/api/v1/reports/catalog":
                self._send_json(200, report_service.catalog())
                return
            if path == "/api/v1/state":
                try:
                    loaded = store.load()
                    context = self._auth_context()
                    if auth_enabled and loaded.get("state") and context:
                        permitted_arrays = {"visits": "visits.view", "cards": "cards.view", "groups": "groups.view", "events": "events.view", "audit": "audit.view"}
                        snapshot = loaded["state"]
                        for key, permission in permitted_arrays.items():
                            if permission not in context["permissions"]:
                                snapshot[key] = []
                        if "dashboard.view" not in context["permissions"]:
                            snapshot["activity"] = []
                        loaded["state"] = snapshot
                    self._send_json(200, loaded)
                except Exception:
                    logging.exception("Unable to load prototype state")
                    self._send_json(500, {"ok": False, "error": "STATE_LOAD_FAILED", "message": "Saved state could not be loaded. Stop and preserve the local database for investigation."})
                return
            if path == "/api/v1/visitor-types":
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    include_inactive = (query.get("includeInactive") or ["1"])[0].strip().lower() in {"1", "true", "yes"}
                    self._send_json(200, domain_store.list_visitor_types(include_inactive=include_inactive))
                except Exception:
                    logging.exception("Unable to list visitor types")
                    self._send_json(500, {"ok": False, "error": "VISITOR_TYPE_LIST_FAILED", "message": "Visitor types could not be loaded from local SQLite."})
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
            if path == "/api/v1/group-visits/status":
                try:
                    self._send_json(200, group_store.status())
                except Exception:
                    logging.exception("Unable to inspect group-visit feature status")
                    self._send_json(500, {"ok": False, "error": "GROUP_VISIT_STATUS_FAILED", "message": "Group-visit status could not be read."})
                return
            if path == "/api/v1/group-visits":
                try:
                    self._send_json(200, group_store.list_bookings())
                except GroupVisitError as exc:
                    self._send_group_error(exc)
                except Exception:
                    logging.exception("Unable to list group visits")
                    self._send_json(500, {"ok": False, "error": "GROUP_VISIT_LIST_FAILED", "message": "Group visits could not be loaded."})
                return
            group_path = re.fullmatch(r"/api/v1/group-visits/([^/]+)", path)
            if group_path:
                try:
                    self._send_json(200, {"ok": True, "group": group_store.get_booking(unquote(group_path.group(1)))})
                except GroupVisitError as exc:
                    self._send_group_error(exc)
                except Exception:
                    logging.exception("Unable to read group visit")
                    self._send_json(500, {"ok": False, "error": "GROUP_VISIT_READ_FAILED", "message": "The requested group visit could not be loaded."})
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
            path = urlsplit(self.path).path
            if not self._require_permission("PUT", path):
                return
            if path != "/api/v1/state":
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

            if path in {"/api/v1/auth/login", "/api/v1/auth/bootstrap", "/api/v1/auth/logout"}:
                if not isinstance(payload, dict):
                    self._send_json(400, {"ok": False, "error": "INVALID_JSON_OBJECT", "message": "Request body must be a JSON object."})
                    return
                if self._handle_auth_post(path, payload):
                    return
            if path.startswith("/api/v1/") and not self._require_permission("POST", path):
                return
            if auth_enabled and isinstance(payload, dict):
                live_confirm = re.fullmatch(r"/api/v1/group-visits/([^/]+)/confirm", path)
                if live_confirm:
                    try:
                        pending_scan = group_store.get_scan(unquote(live_confirm.group(1)).upper(), str(payload.get("scanId", "")))
                    except Exception:
                        pending_scan = None  # Let the normal domain handler return the specific invalid/expired scan error.
                    context = self._auth_context()
                    if pending_scan and pending_scan.get("mode") == "live" and (not context or "attendance.manage" not in context.get("permissions", [])):
                        self._send_json(403, {"ok": False, "error": "PERMISSION_DENIED", "message": "Live group attendance confirmation requires attendance-management permission.", "requiredPermissions": ["attendance.manage", "groups.manage"]})
                        return
            try:
                if path.startswith("/api/v1/access/") and self._handle_access_control_post(path, payload):
                    return
                if path == "/api/v1/reports/preview":
                    self._send_json(200, report_service.preview(payload))
                    return
                if path == "/api/v1/reports/export":
                    filename, content_type, body, meta = report_service.export(payload)
                    self._send_download(200, content_type, filename, body)
                    return
                if path == "/api/v1/integrations/sms/send":
                    result = integration_hub.send_sms(payload)
                    self._send_json(200 if result.get("alreadyApplied") else 201, result)
                    return
                if path == "/api/v1/visitor-types":
                    result = domain_store.create_visitor_type(payload)
                    self._send_json(201, result)
                    return
                visitor_type_update_match = re.fullmatch(r"/api/v1/visitor-types/([a-z0-9-]+)/update", path, re.I)
                if visitor_type_update_match:
                    type_id = visitor_type_update_match.group(1).lower()
                    result = domain_store.update_visitor_type(type_id, payload)
                    self._send_json(200, result)
                    return
                if path == "/api/v1/group-visits":
                    group = group_store.create_booking(payload)
                    self._send_json(201, {"ok": True, "group": group})
                    return
                if path == "/api/v1/group-visits/qr/validate":
                    token = payload.get("groupQrToken") if isinstance(payload, dict) else None
                    self._send_json(200, group_store.validate_qr(token))
                    return
                if path == "/api/v1/group-visits/qr/svg":
                    token = payload.get("groupQrToken") if isinstance(payload, dict) else None
                    self._send_svg(200, group_store.qr_svg(token))
                    return
                group_qr_rotate_match = re.fullmatch(r"/api/v1/group-visits/([^/]+)/qr/rotate", path)
                if group_qr_rotate_match:
                    self._send_json(200, group_store.rotate_qr(unquote(group_qr_rotate_match.group(1))))
                    return
                group_roster_match = re.fullmatch(r"/api/v1/group-visits/([^/]+)/roster", path)
                if group_roster_match:
                    group = group_store.add_roster(unquote(group_roster_match.group(1)), payload)
                    self._send_json(200, {"ok": True, "group": group})
                    return
                group_scan_match = re.fullmatch(r"/api/v1/group-visits/([^/]+)/scan", path)
                if group_scan_match:
                    group_id = unquote(group_scan_match.group(1)).upper()
                    mode = group_store.status()["mode"]
                    scan_meta = group_store.create_scan(group_id, payload, mode=mode)
                    if mode == "mock":
                        match_result = group_store.mock_match_result(group_id, scan_meta, payload["imageWidth"], payload["imageHeight"])
                    else:
                        booking = group_store.get_booking(group_id)
                        approved_profile_ids = [member["faceProfileId"] for member in booking["roster"] if member.get("faceProfileId")]
                        if not approved_profile_ids:
                            match_result = {"ok": True, "mode": "live", "simulated": False, "matchingAvailable": False, "errorCode": "NO_APPROVED_ROSTER_PROFILES", "message": "Photo captured. No roster member has an active approved face profile; use the manual roster reconciliation workflow. No identity was inferred.", "groupEventId": group_id, "scanId": scan_meta["scanId"], "eventType": payload.get("eventType"), "detectedFaceCount": 0, "imageWidth": payload["imageWidth"], "imageHeight": payload["imageHeight"], "candidates": [], "groupImageUsedForEnrollment": False, "humanRosterReconciliationRequired": True}
                        else:
                            try:
                                match_result = face_store.match_group({"imageData": payload.get("imageData"), "eventType": payload.get("eventType"), "groupEventId": group_id, "sessionId": group_id, "studentIds": approved_profile_ids})
                                member_by_profile = {member["faceProfileId"]: member for member in booking["roster"] if member.get("faceProfileId")}
                                for candidate in match_result.get("candidates", []):
                                    profile = candidate.get("candidate", {}) if isinstance(candidate, dict) else {}
                                    matched_member = member_by_profile.get(profile.get("studentId"))
                                    if matched_member:
                                        candidate["memberId"] = matched_member["memberId"]
                                match_result["mode"] = "live"
                                match_result["simulated"] = False
                                match_result["matchingAvailable"] = True
                                match_result["scanId"] = scan_meta["scanId"]
                                match_result["groupImageUsedForEnrollment"] = False
                            except FaceAttendanceError as exc:
                                # The photo remains available for human roster reconciliation, but there is no mock fallback in live mode.
                                match_result = {"ok": True, "mode": "live", "simulated": False, "matchingAvailable": False, "errorCode": exc.code, "message": f"Group photo captured; live matching is unavailable ({exc.message}). Review the roster manually or correct the approval/device configuration and capture again.", "groupEventId": group_id, "scanId": scan_meta["scanId"], "eventType": payload.get("eventType"), "detectedFaceCount": 0, "imageWidth": payload["imageWidth"], "imageHeight": payload["imageHeight"], "candidates": [], "groupImageUsedForEnrollment": False, "humanRosterReconciliationRequired": True}
                    match_result.update({"scanId": scan_meta["scanId"], "groupPhotoRetained": scan_meta["photoRetained"], "photoRetentionStatus": scan_meta["photoRetentionStatus"], "photoRetentionReason": scan_meta["photoRetentionReason"]})
                    group_store.update_scan_match(scan_meta["scanId"], match_result)
                    self._send_json(200, match_result)
                    return
                group_confirm_match = re.fullmatch(r"/api/v1/group-visits/([^/]+)/confirm", path)
                if group_confirm_match:
                    group_id = unquote(group_confirm_match.group(1)).upper()
                    if not isinstance(payload, dict):
                        raise GroupVisitError(400, "INVALID_CONFIRMATION", "Request body must be a JSON object.")
                    mode = group_store.get_scan(group_id, str(payload.get("scanId", "")))["mode"]
                    if mode == "mock":
                        result = group_store.confirm_mock_scan(group_id, payload)
                    else:
                        decision = group_store.validate_live_confirmations(group_id, str(payload.get("scanId", "")), payload.get("eventType"), payload.get("confirmations"), payload.get("operator"))
                        face_result = face_store.confirm_group_batch({"groupEventId": group_id, "sessionId": group_id, "operator": decision["operator"], "confirmBatch": True, "confirmations": decision["faceConfirmations"]})
                        result = group_store.finalize_live_scan(group_id, payload, face_result)
                        result["faceAttendanceEvents"] = face_result.get("events", [])
                        result["genetecSyncQueuedCount"] = face_result.get("genetecSyncQueuedCount", 0)
                        result["genetecEventRaisedCount"] = face_result.get("genetecEventRaisedCount", 0)
                    self._send_json(201, result)
                    return
                group_reconcile_match = re.fullmatch(r"/api/v1/group-visits/([^/]+)/reconcile", path)
                if group_reconcile_match:
                    group = group_store.reconcile(unquote(group_reconcile_match.group(1)), payload)
                    self._send_json(200, {"ok": True, "group": group})
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
                if path == "/api/v1/face-attendance/genetec-sync/retry":
                    self._send_json(200, face_store.retry_pending_genetec_attendance(payload))
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
            except ReportingError as exc:
                self._send_json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})
            except AccessControlError as exc:
                self._send_json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": "INVALID_ACCESS_CONTROL_INPUT", "message": str(exc)[:240]})
            except DomainError as exc:
                self._send_domain_error(exc)
            except FaceAttendanceError as exc:
                self._send_face_error(exc)
            except GroupVisitError as exc:
                self._send_group_error(exc)
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
        logging.info("Reports API: GET /api/v1/reports/catalog; POST /api/v1/reports/preview|export (CSV, XLSX, JSON, PDF, HTML)")
        logging.info("Group visit API: GET /api/v1/group-visits/status|/api/v1/group-visits; POST /api/v1/group-visits, /qr/validate|svg, /{groupId}/qr/rotate|roster|scan|confirm|reconcile")
        logging.info("Optional gated face attendance API: GET /api/v1/face-attendance/status; POST /api/v1/face-attendance/enroll|match|confirm|group-match|group-confirm-batch")
        logging.info("Approved local Genetec student roster API: GET /api/v1/genetec/student-roster/groups; POST /api/v1/genetec/student-roster/import; GET /api/v1/genetec/student-roster; GET /api/v1/genetec/student-roster/{guid}/portrait; POST /api/v1/genetec/student-roster/{guid}/attendance")
        logging.warning("PROTOTYPE ONLY: local account authentication and feature permissions are enabled; this is not an enterprise identity provider; use synthetic data only and do not expose beyond loopback")
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

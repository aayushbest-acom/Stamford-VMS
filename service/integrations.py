"""Optional prototype adapters for SMS and integration diagnostics.

The default is a local-only mock. Live providers are enabled only by explicit
environment configuration and a recipient allow-list. API secrets are never
returned to the UI or written to the database/logs.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid

E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
SMS_MODES = {"mock", "vonage", "twilio"}
MAX_SMS_CHARS = 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 5:
        return "••••"
    return "+" + "•" * max(0, len(digits) - 4) + digits[-4:]


class IntegrationError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


class IntegrationHub:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS integration_sms_outbox (
                    message_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    recipient_display TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    body_preview TEXT NOT NULL DEFAULT '',
                    body_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_message_id TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_sms_outbox_created ON integration_sms_outbox(created_at_utc DESC);
            """)

    @staticmethod
    def _sms_mode() -> str:
        mode = os.environ.get("STAMFORD_SMS_MODE", "mock").strip().lower()
        return mode if mode in SMS_MODES else "mock"

    def _provider_config(self) -> dict:
        mode = self._sms_mode()
        if mode == "mock":
            return {"mode": "mock", "provider": "Local mock", "configured": True, "live": False, "ready": True, "missing": []}
        if mode == "vonage":
            required = {"VONAGE_API_KEY": os.environ.get("VONAGE_API_KEY", "").strip(),
                        "VONAGE_API_SECRET": os.environ.get("VONAGE_API_SECRET", "").strip(),
                        "VONAGE_FROM": os.environ.get("VONAGE_FROM", "StamfordVMS").strip()}
            missing = [name for name, value in required.items() if not value]
            return {"mode": mode, "provider": "Vonage SMS API", "configured": not missing,
                    "live": True, "ready": not missing and self._allowed_recipients(), "missing": missing}
        required = {"TWILIO_ACCOUNT_SID": os.environ.get("TWILIO_ACCOUNT_SID", "").strip(),
                    "TWILIO_AUTH_TOKEN": os.environ.get("TWILIO_AUTH_TOKEN", "").strip(),
                    "TWILIO_FROM_NUMBER": os.environ.get("TWILIO_FROM_NUMBER", "").strip()}
        missing = [name for name, value in required.items() if not value]
        return {"mode": mode, "provider": "Twilio Programmable Messaging", "configured": not missing,
                "live": True, "ready": not missing and self._allowed_recipients(), "missing": missing}

    @staticmethod
    def _is_safe_pass_template(template: str) -> bool:
        if not isinstance(template, str) or not template.startswith("https://") or "{token}" not in template:
            return False
        try:
            parsed = urllib.parse.urlsplit(template.replace("{token}", "OPAQUE_TOKEN"))
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                return False
            hostname = parsed.hostname.lower()
            if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
                return False
            try:
                address = ipaddress.ip_address(hostname)
                if not address.is_global:
                    return False
            except ValueError:
                pass
            return True
        except ValueError:
            return False

    @staticmethod
    def _allowed_recipients() -> set[str]:
        values = os.environ.get("STAMFORD_SMS_ALLOWED_RECIPIENTS", "")
        return {x.strip() for x in values.split(",") if x.strip()}

    def status(self) -> dict:
        config = self._provider_config()
        allowlist_present = bool(self._allowed_recipients())
        if config["mode"] == "mock":
            reason = "Local mock outbox is enabled. No SMS is sent to a handset."
        elif config["missing"]:
            reason = "Live provider credentials are missing: " + ", ".join(config["missing"])
        elif not allowlist_present:
            reason = "Live sending is blocked until STAMFORD_SMS_ALLOWED_RECIPIENTS contains one or more explicitly approved E.164 test numbers."
        else:
            reason = "Live provider is configured. Only recipients in the local allow-list may receive messages."
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM integration_sms_outbox").fetchone()
        pass_template = os.environ.get("STAMFORD_PUBLIC_PASS_URL_TEMPLATE", "").strip()
        pass_template_configured = self._is_safe_pass_template(pass_template)
        if config["mode"] != "mock" and config["ready"] and not pass_template_configured:
            reason += " Visitor-invitation SMS is additionally blocked until STAMFORD_PUBLIC_PASS_URL_TEMPLATE points to a hosted HTTPS pass page and contains {token}. Test SMS remains available."
        return {
            "ok": True,
            "mode": "prototype-only",
            "sms": {"mode": config["mode"], "provider": config["provider"], "configured": config["configured"],
                    "liveProvider": config["live"], "ready": bool(config["ready"]),
                    "allowlistConfigured": allowlist_present, "reason": reason,
                    "publicPassTemplateConfigured": pass_template_configured,
                    "outboxCount": int(row["total"]),
                    "maximumMessageCharacters": MAX_SMS_CHARS},
            "printer": {"mode": "browser-windows-print-dialog", "available": True, "directSpooler": False,
                        "statusMeaning": "The browser can open the native Windows print dialog but cannot certify that paper physically printed. Verify the printer queue/output."},
            "camera": {"mode": "browser-media-device", "available": "browser-detected",
                       "inference": "not-configured-by-this-adapter", "privacy": "Preview/capture remains in the browser unless a workflow explicitly submits the frame."},
            "genetec": {"mode": "separate-local-bridge", "endpoint": "http://127.0.0.1:5190/api/genetec/status",
                         "statusMustBeProbedSeparately": True},
        }

    def list_outbox(self, limit: int = 30) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("""SELECT message_id,created_at_utc,provider,mode,recipient_display,purpose,body_preview,
                                status,provider_message_id,error_code,detail FROM integration_sms_outbox
                                ORDER BY created_at_utc DESC LIMIT ?""", (max(1, min(int(limit), 100)),)).fetchall()
        return [dict(row) for row in rows]

    def send_sms(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise IntegrationError(400, "INVALID_REQUEST", "Request body must be a JSON object.")
        to = payload.get("to")
        body = payload.get("message")
        purpose = payload.get("purpose", "test")
        key = payload.get("idempotencyKey") or str(uuid.uuid4())
        if not isinstance(to, str) or not E164_RE.fullmatch(to.strip()):
            raise IntegrationError(400, "INVALID_RECIPIENT", "Recipient must use E.164 format, e.g. +6591234567.")
        to = to.strip()
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_SMS_CHARS:
            raise IntegrationError(400, "INVALID_MESSAGE", f"Message must contain 1–{MAX_SMS_CHARS} characters.")
        if not isinstance(purpose, str) or purpose not in {"test", "invitation", "host-notification", "overdue-alert"}:
            raise IntegrationError(400, "INVALID_PURPOSE", "SMS purpose must be test, invitation, host-notification or overdue-alert.")
        if not isinstance(key, str) or not key.strip() or len(key) > 120:
            raise IntegrationError(400, "INVALID_IDEMPOTENCY_KEY", "idempotencyKey must be a non-empty string of at most 120 characters.")

        config = self._provider_config()
        mode = config["mode"]
        if purpose == "invitation":
            if "{PASS_URL}" not in body:
                raise IntegrationError(400, "INVITATION_URL_PLACEHOLDER_REQUIRED", "Invitation messages must include the {PASS_URL} placeholder.")
            if mode == "mock":
                body = body.replace("{PASS_URL}", "DEMO ONLY: QR pass is available in the local VMS visit details; no public link is configured.")
            else:
                template = os.environ.get("STAMFORD_PUBLIC_PASS_URL_TEMPLATE", "").strip()
                pass_token = payload.get("passToken")
                if not self._is_safe_pass_template(template):
                    raise IntegrationError(503, "PUBLIC_PASS_URL_NOT_CONFIGURED", "Live invitation SMS requires STAMFORD_PUBLIC_PASS_URL_TEMPLATE pointing to a hosted HTTPS pass page with a {token} placeholder. The current prototype is loopback-only and must not be exposed directly.")
                if not isinstance(pass_token, str) or len(pass_token) < 24 or len(pass_token) > 128 or not re.fullmatch(r"[A-Za-z0-9._~-]+", pass_token):
                    raise IntegrationError(400, "SECURE_PASS_TOKEN_REQUIRED", "Live invitation SMS requires the active opaque secure QR token; rotate/reissue the pass if it is not available in this session.")
                public_pass_url = template.replace("{token}", urllib.parse.quote(pass_token, safe=""))
                body = body.replace("{PASS_URL}", public_pass_url)
            if len(body) > MAX_SMS_CHARS:
                raise IntegrationError(400, "INVITATION_MESSAGE_TOO_LONG", f"The rendered invitation message exceeds {MAX_SMS_CHARS} characters.")
        if mode != "mock":
            if config["missing"]:
                raise IntegrationError(503, "SMS_PROVIDER_NOT_CONFIGURED", "Live SMS provider configuration is incomplete.", {"missing": config["missing"]})
            allowed = self._allowed_recipients()
            if not allowed:
                raise IntegrationError(403, "SMS_ALLOWLIST_REQUIRED", "Live SMS is disabled until an explicit recipient allow-list is configured.")
            if to not in allowed:
                raise IntegrationError(403, "SMS_RECIPIENT_NOT_ALLOWED", "Live SMS is restricted to explicitly approved test recipients.")

        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        message_id = str(uuid.uuid4())
        now = utc_now()
        preview = body[:280] if mode == "mock" else "[Live message body not retained; SHA-256 recorded for correlation]"
        try:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute("SELECT * FROM integration_sms_outbox WHERE idempotency_key=?", (key.strip(),)).fetchone()
                if existing:
                    conn.execute("COMMIT")
                    prior = dict(existing)
                    if prior["body_sha256"] != body_hash or prior["recipient_display"] != mask_phone(to):
                        raise IntegrationError(409, "IDEMPOTENCY_KEY_REUSED", "This idempotency key was already used for a different recipient or message.")
                    if prior["status"] in {"Sending", "Unknown"}:
                        raise IntegrationError(409, "SMS_OUTCOME_UNCERTAIN", "A previous attempt may have reached the provider. Inspect provider logs before retrying with a new key.", {"messageId": prior["message_id"], "status": prior["status"]})
                    return {"ok": True, "messageId": prior["message_id"], "status": prior["status"], "mode": prior["mode"],
                            "provider": prior["provider"], "recipient": prior["recipient_display"], "alreadyApplied": True,
                            "providerMessageId": prior["provider_message_id"]}
                conn.execute("""INSERT INTO integration_sms_outbox(message_id,idempotency_key,created_at_utc,updated_at_utc,
                    provider,mode,recipient_display,purpose,body_preview,body_sha256,status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (message_id, key.strip(), now, now, config["provider"], mode,
                     mask_phone(to), purpose, preview, body_hash, "Sending" if mode != "mock" else "Simulated"))
                conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            raise IntegrationError(409, "SMS_REQUEST_CONFLICT", "The SMS request conflicts with an existing outbox entry.") from exc

        if mode == "mock":
            self._update_outbox(message_id, "Simulated", detail="Mock only; no handset delivery attempted.")
            return {"ok": True, "messageId": message_id, "status": "Simulated", "mode": mode,
                    "provider": config["provider"], "recipient": mask_phone(to), "alreadyApplied": False,
                    "note": "Stored in the local mock outbox; no SMS was sent."}

        try:
            provider_result = self._send_provider_sms(mode, to, body)
            self._update_outbox(message_id, "Sent", provider_message_id=provider_result.get("messageId", ""),
                               detail="Provider accepted the request; handset delivery status may require provider callbacks/console.")
            return {"ok": True, "messageId": message_id, "status": "Sent", "mode": mode,
                    "provider": config["provider"], "recipient": mask_phone(to), "alreadyApplied": False,
                    "providerMessageId": provider_result.get("messageId", ""),
                    "note": "Provider accepted the SMS. This is not proof of final handset delivery."}
        except IntegrationError as exc:
            self._update_outbox(message_id, "Failed", error_code=exc.code, detail=exc.message)
            raise
        except (TimeoutError, urllib.error.URLError) as exc:
            # A network timeout can happen after provider acceptance. Do not auto-retry.
            self._update_outbox(message_id, "Unknown", error_code="PROVIDER_OUTCOME_UNKNOWN",
                               detail="Provider response was not received. Inspect provider console before retrying.")
            raise IntegrationError(502, "SMS_PROVIDER_OUTCOME_UNKNOWN", "No definitive provider response was received. Inspect provider logs before attempting a new send.", {"messageId": message_id}) from exc

    def _update_outbox(self, message_id: str, status: str, provider_message_id: str = "", error_code: str = "", detail: str = "") -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE integration_sms_outbox SET updated_at_utc=?,status=?,provider_message_id=?,error_code=?,detail=? WHERE message_id=?",
                         (utc_now(), status, provider_message_id[:180], error_code[:80], detail[:400], message_id))

    def _send_provider_sms(self, mode: str, to: str, message: str) -> dict:
        if mode == "vonage":
            payload = urllib.parse.urlencode({
                "api_key": os.environ["VONAGE_API_KEY"].strip(),
                "api_secret": os.environ["VONAGE_API_SECRET"].strip(),
                "from": os.environ.get("VONAGE_FROM", "StamfordVMS").strip(),
                "to": to,
                "text": message,
                "type": "text",
            }).encode("utf-8")
            request = urllib.request.Request("https://rest.nexmo.com/sms/json", data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            response_data = self._urlopen_json(request)
            items = response_data.get("messages") or []
            item = items[0] if items else {}
            if str(item.get("status", "1")) != "0":
                code = str(item.get("status", "VONAGE_REJECTED"))
                raise IntegrationError(502, "SMS_PROVIDER_REJECTED", "Vonage rejected the SMS request.", {"providerCode": code})
            return {"messageId": str(item.get("message-id", ""))}
        if mode == "twilio":
            sid = os.environ["TWILIO_ACCOUNT_SID"].strip()
            token = os.environ["TWILIO_AUTH_TOKEN"].strip()
            sender = os.environ["TWILIO_FROM_NUMBER"].strip()
            payload = urllib.parse.urlencode({"To": to, "From": sender, "Body": message}).encode("utf-8")
            encoded = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
            request = urllib.request.Request(
                f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(sid, safe='')}/Messages.json",
                data=payload, headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {encoded}"}, method="POST")
            response_data = self._urlopen_json(request)
            if not response_data.get("sid"):
                raise IntegrationError(502, "SMS_PROVIDER_REJECTED", "Twilio did not return a message identifier.")
            return {"messageId": str(response_data.get("sid", ""))}
        raise IntegrationError(500, "UNKNOWN_SMS_PROVIDER", "The configured SMS provider is not supported.")

    @staticmethod
    def _urlopen_json(request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(1024 * 1024).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # Never expose provider response bodies: they can contain numbers or account details.
            raise IntegrationError(502, "SMS_PROVIDER_HTTP_ERROR", f"SMS provider returned HTTP {exc.code}.", {"providerHttpStatus": exc.code}) from exc
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IntegrationError(502, "SMS_PROVIDER_INVALID_RESPONSE", "SMS provider returned a non-JSON response.") from exc
        if not isinstance(value, dict):
            raise IntegrationError(502, "SMS_PROVIDER_INVALID_RESPONSE", "SMS provider response had an unexpected format.")
        return value

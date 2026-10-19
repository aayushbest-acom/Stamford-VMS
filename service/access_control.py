"""Local accounts, feature-level permissions, sessions and access audit for Stamford VMS.

This is suitable for the loopback prototype only. Use a supported identity provider for production.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

PBKDF2_ITERATIONS = 310_000
SESSION_HOURS = 8
PASSWORD_MIN_LENGTH = 12
USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$")
GROUP_NAME_RE = re.compile(r"^[^\x00-\x1f<>]{2,80}$")

PERMISSIONS = [
    {"key": "dashboard.view", "label": "View dashboard", "category": "Dashboard", "description": "Open the overview and summary widgets."},
    {"key": "visits.view", "label": "View visitor register", "category": "Visitors", "description": "Read the visitor register and visit details."},
    {"key": "visits.create", "label": "Create invitations", "category": "Visitors", "description": "Create new visitor invitations."},
    {"key": "visits.manage", "label": "Amend and cancel visits", "category": "Visitors", "description": "Change schedules, rotate QR passes and cancel visits."},
    {"key": "visits.kiosk", "label": "Visitor check-in/out kiosk", "category": "Visitors", "description": "Validate visitor QR and record arrival/departure."},
    {"key": "visitor_types.view", "label": "View visitor types", "category": "Configuration", "description": "Read the visitor-type catalogue."},
    {"key": "visitor_types.manage", "label": "Manage visitor types", "category": "Configuration", "description": "Create, rename and deactivate visitor types."},
    {"key": "groups.view", "label": "View group visits", "category": "Groups", "description": "Read group bookings and roster status."},
    {"key": "groups.manage", "label": "Manage group visits", "category": "Groups", "description": "Create bookings, capture scans, confirm attendance and reconcile rosters."},
    {"key": "attendance.view", "label": "View attendance", "category": "Attendance", "description": "Read attendance events and approved roster profiles."},
    {"key": "attendance.manage", "label": "Record/manage attendance", "category": "Attendance", "description": "Confirm events, manage approved face profiles and reconcile attendance."},
    {"key": "cards.view", "label": "View replacement-card queue", "category": "Replacement cards", "description": "Read replacement-card requests."},
    {"key": "cards.manage", "label": "Process replacement cards", "category": "Replacement cards", "description": "Approve, exception-route and record card collection."},
    {"key": "integrations.view", "label": "View integrations", "category": "Integrations", "description": "Read adapter and delivery status."},
    {"key": "integrations.manage", "label": "Manage integrations", "category": "Integrations", "description": "Send approved test messages and change integration operation settings."},
    {"key": "genetec.view", "label": "View Genetec", "category": "Genetec", "description": "Read connection, imported roster and sync state."},
    {"key": "genetec.manage", "label": "Perform Genetec operations", "category": "Genetec", "description": "Import approved roster data and request approved synchronization actions."},
    {"key": "events.view", "label": "View AI event review", "category": "AI events", "description": "Read event candidates and evidence context."},
    {"key": "events.manage", "label": "Review/acknowledge AI events", "category": "AI events", "description": "Acknowledge events and record human-review outcomes."},
    {"key": "reports.view", "label": "Preview reports", "category": "Reporting", "description": "View the report catalogue and preview permitted report data."},
    {"key": "reports.export", "label": "Export reports", "category": "Reporting", "description": "Download report data in supported export formats."},
    {"key": "audit.view", "label": "View audit trail", "category": "Governance", "description": "Read the system and security audit trail."},
    {"key": "snapshot.view", "label": "View prototype dashboard snapshot", "category": "Prototype data", "description": "Read the synthetic UI snapshot required by legacy prototype widgets."},
    {"key": "snapshot.manage", "label": "Save prototype dashboard snapshot", "category": "Prototype data", "description": "Save synthetic UI widget state. Does not grant access to transactional APIs."},
    {"key": "accounts.manage", "label": "Manage accounts", "category": "Access control", "description": "Create, update, disable and reset account credentials."},
    {"key": "permissions.manage", "label": "Manage groups and permissions", "category": "Access control", "description": "Edit permission groups and per-account overrides."},
]
PERMISSION_KEYS = {item["key"] for item in PERMISSIONS}
ALL_PERMISSION_KEYS = set(PERMISSION_KEYS)

ROLE_GROUPS: dict[str, dict] = {
    "administrator": {"name": "Administrator", "description": "Full system administration and access-control management.", "permissions": ALL_PERMISSION_KEYS},
    "security-operator": {"name": "Security Operator", "description": "Visitor control, security review, attendance and operational integrations.", "permissions": {
        "dashboard.view", "visits.view", "visits.create", "visits.manage", "visits.kiosk", "visitor_types.view", "visitor_types.manage",
        "groups.view", "groups.manage", "attendance.view", "attendance.manage", "cards.view", "integrations.view", "integrations.manage",
        "genetec.view", "genetec.manage", "events.view", "events.manage", "reports.view", "reports.export", "audit.view", "snapshot.view", "snapshot.manage"}},
    "receptionist": {"name": "Receptionist", "description": "Front-desk invitation, check-in/out and visitor-type workflows.", "permissions": {
        "dashboard.view", "visits.view", "visits.create", "visits.manage", "visits.kiosk", "visitor_types.view",
        "groups.view", "groups.manage", "cards.view", "snapshot.view", "snapshot.manage"}},
    "host": {"name": "Host", "description": "Create invitations and review visit-related work as permitted by school policy.", "permissions": {
        "dashboard.view", "visits.create", "visitor_types.view", "snapshot.view"}},
    "card-counter": {"name": "Card Counter Operator", "description": "Process approved replacement-card requests and collection.", "permissions": {
        "dashboard.view", "cards.view", "cards.manage", "attendance.view", "snapshot.view"}},
    "group-organiser": {"name": "Group Organiser", "description": "Manage group bookings and reconcile roster attendance.", "permissions": {
        "dashboard.view", "groups.view", "groups.manage", "visits.create", "visitor_types.view", "snapshot.view"}},
    "auditor": {"name": "Auditor", "description": "Read-only operational reports, records and audit evidence.", "permissions": {
        "dashboard.view", "visits.view", "visitor_types.view", "groups.view", "attendance.view", "cards.view", "integrations.view",
        "genetec.view", "events.view", "reports.view", "reports.export", "audit.view", "snapshot.view"}},
    "read-only": {"name": "Read-only", "description": "View-only access to operational screens assigned by the administrator.", "permissions": {
        "dashboard.view", "visits.view", "visitor_types.view", "groups.view", "attendance.view", "cards.view", "integrations.view",
        "genetec.view", "events.view", "reports.view", "audit.view", "snapshot.view"}},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _password_record(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32)
    return salt, digest


def validate_password(password: object) -> str:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH or len(password) > 256:
        raise ValueError(f"Password must be between {PASSWORD_MIN_LENGTH} and 256 characters.")
    if password.strip() != password or "\x00" in password:
        raise ValueError("Password cannot begin/end with whitespace or contain NUL characters.")
    if len(set(password)) < 5:
        raise ValueError("Choose a less predictable password.")
    return password


def _public_account(row: sqlite3.Row) -> dict:
    return {"accountId": int(row["account_id"]), "username": row["username"], "displayName": row["display_name"], "isActive": bool(row["is_active"]), "createdAtUtc": row["created_at_utc"]}


class AccessControlError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class AccessControlStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=8, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=8000")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
                    created_at_utc TEXT NOT NULL,
                    created_by INTEGER,
                    FOREIGN KEY(created_by) REFERENCES accounts(account_id)
                );
                CREATE TABLE IF NOT EXISTS access_groups (
                    group_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    description TEXT NOT NULL DEFAULT '',
                    built_in INTEGER NOT NULL DEFAULT 0 CHECK(built_in IN (0,1)),
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_permissions (
                    group_id TEXT NOT NULL,
                    permission_key TEXT NOT NULL,
                    PRIMARY KEY(group_id, permission_key),
                    FOREIGN KEY(group_id) REFERENCES access_groups(group_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS account_groups (
                    account_id INTEGER NOT NULL,
                    group_id TEXT NOT NULL,
                    PRIMARY KEY(account_id, group_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE CASCADE,
                    FOREIGN KEY(group_id) REFERENCES access_groups(group_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS account_permission_overrides (
                    account_id INTEGER NOT NULL,
                    permission_key TEXT NOT NULL,
                    effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY(account_id, permission_key),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    revoked_at_utc TEXT,
                    last_seen_at_utc TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    username_key TEXT PRIMARY KEY,
                    failure_count INTEGER NOT NULL,
                    window_started_utc TEXT NOT NULL,
                    last_attempt_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS security_audit (
                    event_id TEXT PRIMARY KEY,
                    timestamp_utc TEXT NOT NULL,
                    actor_account_id INTEGER,
                    actor_username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
            """)
            for group_id, group in ROLE_GROUPS.items():
                conn.execute("INSERT OR IGNORE INTO access_groups(group_id,name,description,built_in,created_at_utc) VALUES(?,?,?,?,?)",
                             (group_id, group["name"], group["description"], 1, _utc_now()))
                if conn.execute("SELECT COUNT(*) FROM group_permissions WHERE group_id=?", (group_id,)).fetchone()[0] == 0:
                    conn.executemany("INSERT OR IGNORE INTO group_permissions(group_id,permission_key) VALUES(?,?)",
                                     [(group_id, key) for key in sorted(group["permissions"])])

    def _audit(self, conn: sqlite3.Connection, actor_id: int | None, actor_username: str, action: str,
               target_type: str, target_id: str, details: dict | None = None) -> None:
        conn.execute("INSERT INTO security_audit VALUES(?,?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), _utc_now(), actor_id, actor_username[:64], action[:80], target_type[:50], str(target_id)[:120],
            json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))[:3000]))

    def account_count(self) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])

    def setup_status(self, token: str | None = None) -> dict:
        context = self.resolve_session(token) if token else None
        return {"ok": True, "setupRequired": self.account_count() == 0, "authenticated": bool(context), "user": context.get("user") if context else None}

    def create_bootstrap_admin(self, username: str, display_name: str, password: str) -> tuple[dict, str, str]:
        username, display_name, password = self._validate_account_fields(username, display_name, password)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] != 0:
                conn.execute("ROLLBACK")
                raise AccessControlError(409, "BOOTSTRAP_CLOSED", "An administrator has already been created. Sign in instead.")
            salt, digest = _password_record(password)
            cur = conn.execute("INSERT INTO accounts(username,display_name,password_salt,password_hash,is_active,created_at_utc) VALUES(?,?,?,?,1,?)",
                               (username, display_name, salt, digest, _utc_now()))
            account_id = int(cur.lastrowid)
            conn.execute("INSERT INTO account_groups(account_id,group_id) VALUES(?, 'administrator')", (account_id,))
            self._audit(conn, account_id, username, "bootstrap_admin_created", "account", str(account_id), {"groupIds": ["administrator"]})
            conn.execute("COMMIT")
        token, csrf = self._create_session(account_id)
        return self.resolve_session(token), token, csrf

    @staticmethod
    def _validate_account_fields(username: object, display_name: object, password: object | None = None) -> tuple[str, str, str | None]:
        if not isinstance(username, str) or not USERNAME_RE.fullmatch(username.strip()):
            raise ValueError("Username must be 3–64 characters and use letters, numbers, dot, underscore or hyphen.")
        username = username.strip().lower()
        if not isinstance(display_name, str) or not (1 <= len(display_name.strip()) <= 100) or any(ord(c) < 32 for c in display_name):
            raise ValueError("Display name must contain 1–100 printable characters.")
        display_name = display_name.strip()
        clean_password = validate_password(password) if password is not None else None
        return username, display_name, clean_password

    def _create_session(self, account_id: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO sessions(token_hash,account_id,csrf_token,created_at_utc,expires_at_utc,last_seen_at_utc) VALUES(?,?,?,?,?,?)",
                         (hashlib.sha256(token.encode()).hexdigest(), account_id, csrf, now.isoformat(timespec="seconds").replace("+00:00", "Z"), expires, now.isoformat(timespec="seconds").replace("+00:00", "Z")))
        return token, csrf

    def login(self, username: str, password: str) -> tuple[dict, str, str]:
        clean_username = username.strip().lower() if isinstance(username, str) else ""
        key = clean_username[:64] or "<empty>"
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as conn:
            attempt = conn.execute("SELECT failure_count,window_started_utc FROM login_attempts WHERE username_key=?", (key,)).fetchone()
            if attempt and now - _parse_utc(attempt["window_started_utc"]) < timedelta(minutes=15) and int(attempt["failure_count"]) >= 8:
                raise AccessControlError(429, "LOGIN_RATE_LIMITED", "Too many sign-in attempts. Wait 15 minutes before trying again.")
            row = conn.execute("SELECT * FROM accounts WHERE username=? COLLATE NOCASE", (clean_username,)).fetchone()
        valid = False
        safe_password = password if isinstance(password, str) and len(password) <= 256 else ""
        if row:
            _, actual = _password_record(safe_password, bytes(row["password_salt"]))
            valid = bool(row["is_active"]) and hmac.compare_digest(actual, bytes(row["password_hash"]))
        else:
            # Same computational cost for unknown usernames reduces account enumeration by timing.
            _password_record(safe_password, b"StamfordVMSFakeSalt")
        if not valid:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                attempt = conn.execute("SELECT failure_count,window_started_utc FROM login_attempts WHERE username_key=?", (key,)).fetchone()
                if attempt and now - _parse_utc(attempt["window_started_utc"]) < timedelta(minutes=15):
                    count, window = int(attempt["failure_count"]) + 1, attempt["window_started_utc"]
                else:
                    count, window = 1, now.isoformat(timespec="seconds").replace("+00:00", "Z")
                conn.execute("INSERT INTO login_attempts(username_key,failure_count,window_started_utc,last_attempt_utc) VALUES(?,?,?,?) ON CONFLICT(username_key) DO UPDATE SET failure_count=excluded.failure_count,window_started_utc=excluded.window_started_utc,last_attempt_utc=excluded.last_attempt_utc",
                             (key, count, window, now.isoformat(timespec="seconds").replace("+00:00", "Z")))
                self._audit(conn, int(row["account_id"]) if row else None, clean_username[:64] or "unknown", "login_failed", "account", clean_username[:64], {})
                conn.execute("COMMIT")
            raise AccessControlError(401, "INVALID_CREDENTIALS", "Username or password is incorrect, or the account is disabled.")
        account_id = int(row["account_id"])
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM login_attempts WHERE username_key=?", (key,))
            self._audit(conn, account_id, row["username"], "login_succeeded", "account", str(account_id), {})
        token, csrf = self._create_session(account_id)
        return self.resolve_session(token), token, csrf

    def resolve_session(self, token: str | None) -> dict | None:
        if not token or not isinstance(token, str):
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT s.token_hash,s.csrf_token,s.expires_at_utc,a.* FROM sessions s JOIN accounts a ON a.account_id=s.account_id WHERE s.token_hash=? AND s.revoked_at_utc IS NULL", (digest,)).fetchone()
            if not row or not row["is_active"] or _parse_utc(row["expires_at_utc"]) <= datetime.now(timezone.utc):
                return None
            conn.execute("UPDATE sessions SET last_seen_at_utc=? WHERE token_hash=?", (_utc_now(), digest))
            account = _public_account(row)
            group_rows = conn.execute("SELECT g.group_id,g.name FROM access_groups g JOIN account_groups ag ON ag.group_id=g.group_id WHERE ag.account_id=? ORDER BY g.name", (account["accountId"],)).fetchall()
            group_perms = conn.execute("SELECT DISTINCT gp.permission_key FROM group_permissions gp JOIN account_groups ag ON ag.group_id=gp.group_id WHERE ag.account_id=?", (account["accountId"],)).fetchall()
            overrides = conn.execute("SELECT permission_key,effect FROM account_permission_overrides WHERE account_id=?", (account["accountId"],)).fetchall()
            permissions = {r["permission_key"] for r in group_perms}
            for override in overrides:
                if override["effect"] == "allow":
                    permissions.add(override["permission_key"])
                else:
                    permissions.discard(override["permission_key"])
        return {"user": account, "groups": [{"groupId": r["group_id"], "name": r["name"]} for r in group_rows], "permissions": sorted(permissions), "csrfToken": row["csrf_token"], "sessionHash": digest}

    def validate_csrf(self, context: dict | None, supplied: str | None) -> bool:
        return bool(context and isinstance(supplied, str) and hmac.compare_digest(context.get("csrfToken", ""), supplied))

    def logout(self, context: dict) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE sessions SET revoked_at_utc=? WHERE token_hash=? AND revoked_at_utc IS NULL", (_utc_now(), context["sessionHash"]))
            self._audit(conn, context["user"]["accountId"], context["user"]["username"], "logout", "account", str(context["user"]["accountId"]), {})

    def _permissions_for_account(self, conn: sqlite3.Connection, account_id: int) -> set[str]:
        inherited = {r[0] for r in conn.execute("SELECT DISTINCT gp.permission_key FROM group_permissions gp JOIN account_groups ag ON ag.group_id=gp.group_id JOIN accounts a ON a.account_id=ag.account_id WHERE a.account_id=? AND a.is_active=1", (account_id,)).fetchall()}
        for row in conn.execute("SELECT permission_key,effect FROM account_permission_overrides WHERE account_id=?", (account_id,)).fetchall():
            if row["effect"] == "allow": inherited.add(row["permission_key"])
            else: inherited.discard(row["permission_key"])
        return inherited

    def _ensure_active_admin(self, conn: sqlite3.Connection) -> None:
        # Effective permission strings alone are not enough: an active account must retain
        # actual membership in the built-in Administrator role as well.
        rows = conn.execute("SELECT a.account_id FROM accounts a JOIN account_groups ag ON ag.account_id=a.account_id WHERE a.is_active=1 AND ag.group_id='administrator'").fetchall()
        for row in rows:
            perms = self._permissions_for_account(conn, int(row["account_id"]))
            if "accounts.manage" in perms and "permissions.manage" in perms:
                return
        raise AccessControlError(409, "LAST_ADMIN_GUARD", "This change would remove the last active account with both account and permission administration rights.")

    @staticmethod
    def _require_administrator_actor(actor: dict | None) -> None:
        """Keep security-control-plane mutations exclusive to the built-in Administrator role.

        General feature permissions may be delegated, but they must not create a path
        for delegated users to grant themselves administrator-level access.
        """
        memberships = {group.get("groupId") for group in (actor or {}).get("groups", []) if isinstance(group, dict)}
        if "administrator" not in memberships:
            raise AccessControlError(403, "ADMINISTRATOR_ROLE_REQUIRED", "Only an account assigned to the built-in Administrator role can change accounts or permission groups.")

    def list_accounts(self) -> dict:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY username COLLATE NOCASE").fetchall()
            result = []
            for row in rows:
                pub = _public_account(row)
                gid_rows = conn.execute("SELECT g.group_id,g.name FROM access_groups g JOIN account_groups ag ON ag.group_id=g.group_id WHERE ag.account_id=? ORDER BY g.name", (pub["accountId"],)).fetchall()
                over = conn.execute("SELECT permission_key,effect FROM account_permission_overrides WHERE account_id=? ORDER BY permission_key", (pub["accountId"],)).fetchall()
                eff = sorted(self._permissions_for_account(conn, pub["accountId"]))
                result.append({**pub, "groups": [{"groupId": g["group_id"], "name": g["name"]} for g in gid_rows], "overrides": [{"permission": o["permission_key"], "effect": o["effect"]} for o in over], "effectivePermissions": eff})
        return {"ok": True, "accounts": result}

    def list_groups(self) -> dict:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM access_groups ORDER BY built_in DESC,name COLLATE NOCASE").fetchall()
            result = []
            for row in rows:
                perms = sorted(r[0] for r in conn.execute("SELECT permission_key FROM group_permissions WHERE group_id=?", (row["group_id"],)).fetchall())
                count = int(conn.execute("SELECT COUNT(*) FROM account_groups ag JOIN accounts a ON a.account_id=ag.account_id WHERE ag.group_id=? AND a.is_active=1", (row["group_id"],)).fetchone()[0])
                result.append({"groupId": row["group_id"], "name": row["name"], "description": row["description"], "builtIn": bool(row["built_in"]), "activeAccountCount": count, "permissions": perms})
        return {"ok": True, "groups": result}

    def list_audit(self, limit: int = 500) -> dict:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT timestamp_utc,actor_username,action,target_type,target_id,details_json FROM security_audit ORDER BY timestamp_utc DESC LIMIT ?", (max(1, min(int(limit), 1000)),)).fetchall()
        return {"ok": True, "events": [{"timestampUtc": r["timestamp_utc"], "actor": r["actor_username"], "action": r["action"], "targetType": r["target_type"], "targetId": r["target_id"], "details": json.loads(r["details_json"])} for r in rows]}

    def create_account(self, payload: dict, actor: dict) -> dict:
        self._require_administrator_actor(actor)
        username, display_name, password = self._validate_account_fields(payload.get("username"), payload.get("displayName"), payload.get("password"))
        group_ids = payload.get("groupIds", [])
        if not isinstance(group_ids, list) or not group_ids or len(group_ids) > 20 or any(not isinstance(g, str) for g in group_ids) or len(group_ids) != len(set(group_ids)):
            raise ValueError("Select one or more unique, valid access groups.")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            known = {r[0] for r in conn.execute("SELECT group_id FROM access_groups WHERE group_id IN (%s)" % ",".join("?" * len(group_ids)), tuple(group_ids)).fetchall()} if group_ids else set()
            if known != set(group_ids):
                conn.execute("ROLLBACK")
                raise ValueError("One or more selected groups do not exist.")
            salt, digest = _password_record(password)
            try:
                cur = conn.execute("INSERT INTO accounts(username,display_name,password_salt,password_hash,is_active,created_at_utc,created_by) VALUES(?,?,?,?,1,?,?)", (username, display_name, salt, digest, _utc_now(), actor["user"]["accountId"]))
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise AccessControlError(409, "USERNAME_EXISTS", "That username already exists.") from exc
            account_id = int(cur.lastrowid)
            conn.executemany("INSERT INTO account_groups(account_id,group_id) VALUES(?,?)", [(account_id, gid) for gid in group_ids])
            self._audit(conn, actor["user"]["accountId"], actor["user"]["username"], "account_created", "account", str(account_id), {"username": username, "groupIds": group_ids})
            conn.execute("COMMIT")
        return {"ok": True, "accountId": account_id, "username": username}

    def update_account(self, account_id: int, payload: dict, actor: dict) -> dict:
        self._require_administrator_actor(actor)
        display_name = payload.get("displayName")
        active = payload.get("isActive")
        if display_name is not None and (not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 100 or any(ord(c) < 32 for c in display_name)):
            raise ValueError("Display name must contain 1–100 printable characters.")
        if active is not None and not isinstance(active, bool): raise ValueError("isActive must be true or false.")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
            if not row:
                conn.execute("ROLLBACK"); raise AccessControlError(404, "ACCOUNT_NOT_FOUND", "Account not found.")
            if account_id == actor["user"]["accountId"] and active is False:
                conn.execute("ROLLBACK"); raise AccessControlError(409, "CANNOT_DISABLE_SELF", "You cannot disable your own active account.")
            if display_name is not None:
                conn.execute("UPDATE accounts SET display_name=? WHERE account_id=?", (display_name.strip(), account_id))
            if active is not None:
                conn.execute("UPDATE accounts SET is_active=? WHERE account_id=?", (int(active), account_id))
            try: self._ensure_active_admin(conn)
            except AccessControlError:
                conn.execute("ROLLBACK"); raise
            self._audit(conn, actor["user"]["accountId"], actor["user"]["username"], "account_updated", "account", str(account_id), {"isActive": active, "displayNameChanged": display_name is not None})
            conn.execute("COMMIT")
        return {"ok": True}

    def reset_password(self, account_id: int, password: str, actor: dict) -> dict:
        self._require_administrator_actor(actor)
        validate_password(password)
        salt, digest = _password_record(password)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone():
                conn.execute("ROLLBACK"); raise AccessControlError(404, "ACCOUNT_NOT_FOUND", "Account not found.")
            conn.execute("UPDATE accounts SET password_salt=?,password_hash=? WHERE account_id=?", (salt, digest, account_id))
            conn.execute("UPDATE sessions SET revoked_at_utc=? WHERE account_id=? AND revoked_at_utc IS NULL", (_utc_now(), account_id))
            self._audit(conn, actor["user"]["accountId"], actor["user"]["username"], "password_reset", "account", str(account_id), {})
            conn.execute("COMMIT")
        return {"ok": True, "sessionsRevoked": True}

    def assign_groups(self, account_id: int, group_ids: list, actor: dict) -> dict:
        self._require_administrator_actor(actor)
        if not isinstance(group_ids, list) or not group_ids or len(group_ids) > 20 or any(not isinstance(g, str) for g in group_ids) or len(group_ids) != len(set(group_ids)): raise ValueError("Select one or more unique groups.")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone(): conn.execute("ROLLBACK"); raise AccessControlError(404, "ACCOUNT_NOT_FOUND", "Account not found.")
            known = {r[0] for r in conn.execute("SELECT group_id FROM access_groups WHERE group_id IN (%s)" % ",".join("?" * len(group_ids)), tuple(group_ids)).fetchall()}
            if known != set(group_ids): conn.execute("ROLLBACK"); raise ValueError("One or more groups do not exist.")
            conn.execute("DELETE FROM account_groups WHERE account_id=?", (account_id,))
            conn.executemany("INSERT INTO account_groups(account_id,group_id) VALUES(?,?)", [(account_id, gid) for gid in group_ids])
            try: self._ensure_active_admin(conn)
            except AccessControlError: conn.execute("ROLLBACK"); raise
            self._audit(conn, actor["user"]["accountId"], actor["user"]["username"], "account_groups_changed", "account", str(account_id), {"groupIds": group_ids})
            conn.execute("COMMIT")
        return {"ok": True}

    def set_overrides(self, account_id: int, overrides: list, actor: dict) -> dict:
        self._require_administrator_actor(actor)
        if not isinstance(overrides, list) or len(overrides) > len(PERMISSION_KEYS): raise ValueError("Overrides must be a list within the permission catalogue size.")
        normalized = {}
        for item in overrides:
            if not isinstance(item, dict) or item.get("permission") not in PERMISSION_KEYS or item.get("effect") not in {"allow", "deny"}:
                raise ValueError("Every override needs a known permission and effect 'allow' or 'deny'.")
            if item["permission"] in normalized: raise ValueError("Duplicate permission override.")
            normalized[item["permission"]] = item["effect"]
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone(): conn.execute("ROLLBACK"); raise AccessControlError(404, "ACCOUNT_NOT_FOUND", "Account not found.")
            conn.execute("DELETE FROM account_permission_overrides WHERE account_id=?", (account_id,))
            conn.executemany("INSERT INTO account_permission_overrides(account_id,permission_key,effect,updated_at_utc) VALUES(?,?,?,?)", [(account_id, key, effect, _utc_now()) for key, effect in normalized.items()])
            try: self._ensure_active_admin(conn)
            except AccessControlError: conn.execute("ROLLBACK"); raise
            self._audit(conn, actor["user"]["accountId"], actor["user"]["username"], "account_permission_overrides_changed", "account", str(account_id), {"overrides": normalized})
            conn.execute("COMMIT")
        return {"ok": True}

    def create_group(self, payload: dict, actor: dict) -> dict:
        self._require_administrator_actor(actor)
        name, description = payload.get("name"), payload.get("description", "")
        if not isinstance(name, str) or not GROUP_NAME_RE.fullmatch(name.strip()) or not isinstance(description, str) or len(description) > 240:
            raise ValueError("Group name must be 2–80 printable characters; description max 240 characters.")
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:48]
        if not slug: raise ValueError("Group name needs at least one letter or number.")
        group_id = slug
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            suffix = 2
            while conn.execute("SELECT 1 FROM access_groups WHERE group_id=? OR name=? COLLATE NOCASE", (group_id, name.strip())).fetchone():
                group_id = f"{slug[:42]}-{suffix}"; suffix += 1
            conn.execute("INSERT INTO access_groups(group_id,name,description,built_in,created_at_utc) VALUES(?,?,?,0,?)", (group_id, name.strip(), description.strip(), _utc_now()))
            self._audit(conn, actor["user"]["accountId"], actor["user"]["username"], "permission_group_created", "group", group_id, {"name": name.strip()})
            conn.execute("COMMIT")
        return {"ok": True, "groupId": group_id, "name": name.strip()}

    def update_group_permissions(self, group_id: str, permission_keys: list, actor: dict) -> dict:
        self._require_administrator_actor(actor)
        if not isinstance(permission_keys, list) or len(permission_keys) > len(PERMISSION_KEYS) or any(key not in PERMISSION_KEYS for key in permission_keys):
            raise ValueError("Select only permissions from the permission catalogue.")
        if len(permission_keys) != len(set(permission_keys)): raise ValueError("Duplicate permission keys are not allowed.")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            group = conn.execute("SELECT * FROM access_groups WHERE group_id=?", (group_id,)).fetchone()
            if not group: conn.execute("ROLLBACK"); raise AccessControlError(404, "GROUP_NOT_FOUND", "Permission group not found.")
            conn.execute("DELETE FROM group_permissions WHERE group_id=?", (group_id,))
            conn.executemany("INSERT INTO group_permissions(group_id,permission_key) VALUES(?,?)", [(group_id, key) for key in sorted(set(permission_keys))])
            try: self._ensure_active_admin(conn)
            except AccessControlError: conn.execute("ROLLBACK"); raise
            self._audit(conn, actor["user"]["accountId"], actor["user"]["username"], "group_permissions_changed", "group", group_id, {"permissionCount": len(permission_keys)})
            conn.execute("COMMIT")
        return {"ok": True}

    def update_group(self, group_id: str, payload: dict, actor: dict) -> dict:
        self._require_administrator_actor(actor)
        name, description = payload.get("name"), payload.get("description", "")
        if not isinstance(name, str) or not GROUP_NAME_RE.fullmatch(name.strip()) or not isinstance(description, str) or len(description) > 240:
            raise ValueError("Group name must be 2–80 printable characters; description max 240 characters.")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT built_in FROM access_groups WHERE group_id=?", (group_id,)).fetchone()
            if not row: conn.execute("ROLLBACK"); raise AccessControlError(404, "GROUP_NOT_FOUND", "Permission group not found.")
            try:
                conn.execute("UPDATE access_groups SET name=?,description=? WHERE group_id=?", (name.strip(), description.strip(), group_id))
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK"); raise AccessControlError(409, "GROUP_NAME_EXISTS", "A group with that name already exists.") from exc
            self._audit(conn, actor["user"]["accountId"], actor["user"]["username"], "permission_group_updated", "group", group_id, {"name": name.strip()})
            conn.execute("COMMIT")
        return {"ok": True}

    def revoke_all_sessions(self, actor: dict, account_id: int | None = None) -> dict:
        with closing(self._connect()) as conn:
            if account_id is None:
                conn.execute("UPDATE sessions SET revoked_at_utc=? WHERE account_id=? AND revoked_at_utc IS NULL", (_utc_now(), actor["user"]["accountId"]))
                target_id = actor["user"]["accountId"]
            else:
                conn.execute("UPDATE sessions SET revoked_at_utc=? WHERE account_id=? AND revoked_at_utc IS NULL", (_utc_now(), account_id))
                target_id = account_id
            self._audit(conn, actor["user"]["accountId"], actor["user"]["username"], "sessions_revoked", "account", str(target_id), {})
        return {"ok": True}

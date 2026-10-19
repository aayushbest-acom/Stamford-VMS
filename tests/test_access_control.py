from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from service.access_control import AccessControlError, AccessControlStore, PERMISSIONS
from service.domain import DomainStore
from service.vms_service import StateStore, make_handler, required_permissions

ROOT = Path(__file__).resolve().parents[1]


class ApiClient:
    def __init__(self, base: str):
        self.base = base
        self.cookie = ""
        self.csrf = ""

    def request(self, method: str, path: str, body=None, csrf=True):
        raw = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.cookie: headers["Cookie"] = self.cookie
        if csrf and self.csrf: headers["X-Stamford-CSRF"] = self.csrf
        request = Request(self.base + path, data=raw, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                data = response.read().decode("utf-8")
                self._capture(response.headers)
                return response.status, json.loads(data) if data else {}
        except HTTPError as exc:
            data = exc.read().decode("utf-8")
            self._capture(exc.headers)
            return exc.code, json.loads(data) if data else {}

    def _capture(self, headers):
        cookie = headers.get("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";", 1)[0]
        # login/bootstrap responses return the CSRF value; /status also refreshes it after reload.
        # request caller extracts it from JSON in methods below.

    def bootstrap(self, username="admin", display="VMS Administrator", password="CorrectHorse-Battery-9"):
        code, result = self.request("POST", "/api/v1/auth/bootstrap", {"username": username, "displayName": display, "password": password}, csrf=False)
        self.csrf = result.get("csrfToken", "")
        return code, result

    def login(self, username, password):
        code, result = self.request("POST", "/api/v1/auth/login", {"username": username, "password": password}, csrf=False)
        self.csrf = result.get("csrfToken", "")
        return code, result

    def status(self):
        code, result = self.request("GET", "/api/v1/auth/status")
        if result.get("authenticated"):
            self.csrf = result.get("csrfToken", "")
        return code, result


class AccessControlApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ui = self.root / "ui"
        self.ui.mkdir()
        (self.ui / "index.html").write_text("<!doctype html><title>Auth Test</title>", encoding="utf-8")
        db = self.root / "prototype-state.sqlite3"
        self.state = StateStore(db)
        self.domain = DomainStore(db)
        self.access = AccessControlStore(self.root / "access-control.sqlite3")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ui, self.state, self.domain, access_store=self.access, auth_enabled=True))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.admin = ApiClient(self.base)
        self.assertEqual(self.access.account_count(), 0)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def create_admin(self):
        code, result = self.admin.bootstrap()
        self.assertEqual(code, 201, result)
        self.assertIn("accounts.manage", result["permissions"])
        return result

    def create_account(self, username, display, password, group_id):
        code, result = self.admin.request("POST", "/api/v1/access/accounts", {
            "username": username, "displayName": display, "password": password, "groupIds": [group_id]
        })
        self.assertEqual(code, 201, result)
        return int(result["accountId"])

    def test_auth_is_default_and_bootstrap_is_one_time(self):
        code, result = self.admin.request("GET", "/api/v1/reports/catalog")
        self.assertEqual(code, 401)
        self.assertEqual(result["error"], "AUTHENTICATION_REQUIRED")
        _, status = self.admin.status()
        self.assertTrue(status["setupRequired"])
        self.create_admin()
        _, status = self.admin.status()
        self.assertTrue(status["authenticated"])
        code, result = self.admin.bootstrap("second-admin", "Second Admin", "Another-Strong-Password-8")
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "BOOTSTRAP_CLOSED")

    def test_receptionist_has_operational_access_but_not_access_admin_or_reports(self):
        self.create_admin()
        self.create_account("frontdesk", "Front Desk", "FrontDesk-Strong-Password-2", "receptionist")
        client = ApiClient(self.base)
        code, result = client.login("frontdesk", "FrontDesk-Strong-Password-2")
        self.assertEqual(code, 200, result)
        self.assertIn("visits.create", result["permissions"])
        self.assertNotIn("accounts.manage", result["permissions"])
        self.assertEqual(client.request("GET", "/api/v1/visits")[0], 200)
        self.assertEqual(client.request("GET", "/api/v1/access/accounts")[0], 403)
        self.assertEqual(client.request("GET", "/api/v1/reports/catalog")[0], 403)
        self.assertEqual(client.request("POST", "/api/v1/visitor-types", {"name": "Not Allowed", "description": ""})[0], 403)

    def test_csrf_required_for_mutations_and_cookie_session_works(self):
        self.create_admin()
        self.create_account("frontdesk", "Front Desk", "FrontDesk-Strong-Password-2", "receptionist")
        client = ApiClient(self.base)
        self.assertEqual(client.login("frontdesk", "FrontDesk-Strong-Password-2")[0], 200)
        code, result = client.request("POST", "/api/v1/visits", {"id": "VMS-AUTH-01", "name": "Synthetic Visitor"}, csrf=False)
        self.assertEqual(code, 403)
        self.assertEqual(result["error"], "CSRF_VALIDATION_FAILED")
        code, result = client.request("POST", "/api/v1/visits", {
            "id": "VMS-AUTH-01", "name": "Synthetic Visitor", "email": "v@example.test", "host": "Front Desk",
            "destination": "Reception", "visitDate": "2026-10-19", "window": "09:00-10:00", "purpose": "test"
        })
        # The test uses the current school time zone, so the time may be outside the window; either validation error is
        # proof that auth+CSRF allowed the request to the domain validator (not an authorization failure).
        self.assertNotEqual(result.get("error"), "CSRF_VALIDATION_FAILED")
        self.assertNotEqual(result.get("error"), "PERMISSION_DENIED")

    def test_account_override_changes_effective_permissions_and_is_audited(self):
        self.create_admin()
        account_id = self.create_account("auditor1", "Audit Reviewer", "Audit-Strong-Password-4", "read-only")
        user = ApiClient(self.base)
        self.assertEqual(user.login("auditor1", "Audit-Strong-Password-4")[0], 200)
        self.assertEqual(user.request("GET", "/api/v1/reports/catalog")[0], 200)
        # Direct deny overrides the inherited Read-only Reports permission immediately for existing sessions.
        code, result = self.admin.request("POST", f"/api/v1/access/accounts/{account_id}/overrides", {"overrides": [{"permission": "reports.view", "effect": "deny"}]})
        self.assertEqual(code, 200, result)
        self.assertEqual(user.request("GET", "/api/v1/reports/catalog")[0], 403)
        code, result = self.admin.request("POST", f"/api/v1/access/accounts/{account_id}/overrides", {"overrides": [{"permission": "reports.view", "effect": "allow"}]})
        self.assertEqual(code, 200, result)
        self.assertEqual(user.request("GET", "/api/v1/reports/catalog")[0], 200)
        audit = self.admin.request("GET", "/api/v1/access/audit")[1]["events"]
        self.assertTrue(any(e["action"] == "account_permission_overrides_changed" for e in audit))

    def test_group_permission_changes_apply_without_relogin(self):
        self.create_admin()
        self.create_account("host1", "Host One", "Host-Strong-Password-6", "host")
        client = ApiClient(self.base)
        self.assertEqual(client.login("host1", "Host-Strong-Password-6")[0], 200)
        self.assertEqual(client.request("GET", "/api/v1/visits")[0], 403)
        code, result = self.admin.request("POST", "/api/v1/access/groups/host/permissions", {"permissionKeys": ["dashboard.view", "visits.create", "visits.view", "visitor_types.view", "snapshot.view"]})
        self.assertEqual(code, 200, result)
        self.assertEqual(client.request("GET", "/api/v1/visits")[0], 200)

    def test_cannot_remove_last_admin_permission_or_disable_own_admin(self):
        self.create_admin()
        code, result = self.admin.request("POST", "/api/v1/access/groups/administrator/permissions", {"permissionKeys": ["dashboard.view"]})
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "LAST_ADMIN_GUARD")
        code, result = self.admin.request("POST", "/api/v1/access/accounts/1/update", {"isActive": False})
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "CANNOT_DISABLE_SELF")

    def test_disabled_account_sessions_stop_working(self):
        self.create_admin()
        account_id = self.create_account("host1", "Host One", "Host-Strong-Password-6", "host")
        client = ApiClient(self.base)
        self.assertEqual(client.login("host1", "Host-Strong-Password-6")[0], 200)
        code, result = self.admin.request("POST", f"/api/v1/access/accounts/{account_id}/update", {"isActive": False})
        self.assertEqual(code, 200, result)
        self.assertEqual(client.request("GET", "/api/v1/auth/status")[1]["authenticated"], False)
        self.assertEqual(client.request("GET", "/api/v1/visits")[0], 401)

    def test_genetec_enrollment_requires_attendance_and_genetec_permissions(self):
        required = required_permissions("POST", "/api/v1/face-attendance/enroll-from-genetec")
        self.assertEqual(required, ["attendance.manage", "genetec.manage"])

    def test_permission_catalog_has_unique_known_keys(self):
        keys = [p["key"] for p in PERMISSIONS]
        self.assertEqual(len(keys), len(set(keys)))
        code, result = self.admin.bootstrap()
        self.assertEqual(code, 201)
        catalog = self.admin.request("GET", "/api/v1/access/permission-catalog")
        self.assertEqual(catalog[0], 200)
        self.assertEqual({p["key"] for p in catalog[1]["permissions"]}, set(keys))

    def test_delegated_permission_flags_cannot_administer_accounts_or_promote_self(self):
        self.create_admin()
        self.create_account("frontdesk", "Front Desk", "FrontDesk-Strong-Password-2", "receptionist")
        # An administrator may configure a group with powerful flags, but membership in that group
        # must not replace membership in the built-in Administrator role for the security control plane.
        code, result = self.admin.request("POST", "/api/v1/access/groups/receptionist/permissions", {
            "permissionKeys": ["dashboard.view", "visits.view", "visits.create", "accounts.manage", "permissions.manage", "snapshot.view"]
        })
        self.assertEqual(code, 200, result)
        client = ApiClient(self.base)
        self.assertEqual(client.login("frontdesk", "FrontDesk-Strong-Password-2")[0], 200)
        self.assertIn("accounts.manage", client.status()[1]["permissions"])
        self.assertEqual(client.request("GET", "/api/v1/access/accounts")[0], 403)
        code, result = client.request("POST", "/api/v1/access/accounts", {
            "username": "selfadmin", "displayName": "Self Admin", "password": "SelfAdmin-Strong-Password-3", "groupIds": ["administrator"]
        })
        self.assertEqual(code, 403)
        self.assertEqual(result["error"], "ADMINISTRATOR_ROLE_REQUIRED")
        code, result = client.request("POST", "/api/v1/access/groups/host/permissions", {"permissionKeys": ["dashboard.view"]})
        self.assertEqual(code, 403)
        self.assertEqual(result["error"], "ADMINISTRATOR_ROLE_REQUIRED")

    def test_unknown_api_routes_are_denied_after_authentication(self):
        self.create_admin()
        code, result = self.admin.request("GET", "/api/v1/some-future-route")
        self.assertEqual(code, 403)
        self.assertEqual(result["error"], "PERMISSION_MAPPING_REQUIRED")

    def test_last_administrator_must_keep_builtin_role_membership(self):
        self.create_admin()
        # A non-admin may be given both permission keys but cannot satisfy the lockout guard
        # if the only true Administrator is stripped of its role membership.
        code, result = self.admin.request("POST", "/api/v1/access/accounts/1/groups", {"groupIds": ["read-only"]})
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "LAST_ADMIN_GUARD")


if __name__ == "__main__":
    unittest.main(verbosity=2)

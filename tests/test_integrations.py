import json
import os
import tempfile
import urllib.parse
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import MagicMock, patch

from service.integrations import IntegrationHub
from service.vms_service import StateStore, make_handler


class IntegrationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ui = self.root / "ui"
        self.ui.mkdir()
        (self.ui / "index.html").write_text("<!doctype html><title>Test</title>", encoding="utf-8")
        self.db = self.root / "data" / "prototype.sqlite3"
        self.store = StateStore(self.db)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ui, self.store))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(self.base + path, data=data, headers={"Content-Type": "application/json"}, method=method)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_status_defaults_to_local_mock_and_never_exposes_secrets(self):
        with patch.dict(os.environ, {}, clear=True):
            code, data = self.request("GET", "/api/v1/integrations/status")
        self.assertEqual(code, 200)
        self.assertEqual(data["sms"]["mode"], "mock")
        self.assertTrue(data["sms"]["ready"])
        self.assertFalse(data["sms"]["liveProvider"])
        self.assertFalse(data["printer"]["directSpooler"])
        self.assertTrue(data["genetec"]["statusMustBeProbedSeparately"])
        self.assertNotIn("apiSecret", json.dumps(data))

    def test_mock_sms_is_persisted_but_explicitly_simulated(self):
        with patch.dict(os.environ, {"STAMFORD_SMS_MODE": "mock"}, clear=True):
            payload = {"to": "+6591234567", "message": "Prototype test SMS", "purpose": "test", "idempotencyKey": "test-key-1"}
            code, result = self.request("POST", "/api/v1/integrations/sms/send", payload)
            self.assertEqual(code, 201)
            self.assertEqual(result["status"], "Simulated")
            self.assertIn("no SMS was sent", result["note"])
            code, retry = self.request("POST", "/api/v1/integrations/sms/send", payload)
            self.assertEqual(code, 200)
            self.assertTrue(retry["alreadyApplied"])
            code, outbox = self.request("GET", "/api/v1/integrations/sms/outbox")
        self.assertEqual(code, 200)
        self.assertEqual(len(outbox["messages"]), 1)
        self.assertEqual(outbox["messages"][0]["status"], "Simulated")
        self.assertEqual(outbox["messages"][0]["recipient_display"], "+••••••4567")

    def test_sms_validates_e164_and_message_limits(self):
        with patch.dict(os.environ, {"STAMFORD_SMS_MODE": "mock"}, clear=True):
            code, bad_number = self.request("POST", "/api/v1/integrations/sms/send", {"to": "6591234567", "message": "Test"})
            self.assertEqual(code, 400)
            self.assertEqual(bad_number["error"], "INVALID_RECIPIENT")
            code, long_message = self.request("POST", "/api/v1/integrations/sms/send", {"to": "+6591234567", "message": "x" * 1025})
        self.assertEqual(code, 400)
        self.assertEqual(long_message["error"], "INVALID_MESSAGE")

    def test_live_sms_fails_closed_without_credentials_or_allowlist(self):
        with patch.dict(os.environ, {"STAMFORD_SMS_MODE": "vonage"}, clear=True):
            code, missing = self.request("POST", "/api/v1/integrations/sms/send", {"to": "+6591234567", "message": "Test"})
            self.assertEqual(code, 503)
            self.assertEqual(missing["error"], "SMS_PROVIDER_NOT_CONFIGURED")
        env = {"STAMFORD_SMS_MODE": "vonage", "VONAGE_API_KEY": "test-key", "VONAGE_API_SECRET": "test-secret", "VONAGE_FROM": "TestVMS"}
        with patch.dict(os.environ, env, clear=True):
            code, blocked = self.request("POST", "/api/v1/integrations/sms/send", {"to": "+6591234567", "message": "Test"})
        self.assertEqual(code, 403)
        self.assertEqual(blocked["error"], "SMS_ALLOWLIST_REQUIRED")

    def test_live_sms_is_allowlist_restricted(self):
        env = {"STAMFORD_SMS_MODE": "vonage", "VONAGE_API_KEY": "test-key", "VONAGE_API_SECRET": "test-secret", "VONAGE_FROM": "TestVMS", "STAMFORD_SMS_ALLOWED_RECIPIENTS": "+6599990000"}
        with patch.dict(os.environ, env, clear=True):
            code, blocked = self.request("POST", "/api/v1/integrations/sms/send", {"to": "+6591234567", "message": "Test"})
        self.assertEqual(code, 403)
        self.assertEqual(blocked["error"], "SMS_RECIPIENT_NOT_ALLOWED")

    def test_live_vonage_send_sends_request_and_masks_recipient_in_outbox(self):
        env = {"STAMFORD_SMS_MODE": "vonage", "VONAGE_API_KEY": "test-key", "VONAGE_API_SECRET": "test-secret", "VONAGE_FROM": "TestVMS", "STAMFORD_SMS_ALLOWED_RECIPIENTS": "+6591234567"}
        provider_response = MagicMock()
        provider_response.__enter__.return_value.read.return_value = json.dumps({"messages": [{"status": "0", "message-id": "provider-123"}]}).encode()
        with patch.dict(os.environ, env, clear=True), patch("urllib.request.urlopen", return_value=provider_response) as mocked_urlopen:
            code, sent = self.request("POST", "/api/v1/integrations/sms/send", {"to": "+6591234567", "message": "Live provider test", "idempotencyKey": "live-key-1"})
            self.assertEqual(code, 201)
            self.assertEqual(sent["status"], "Sent")
            self.assertEqual(sent["providerMessageId"], "provider-123")
            mocked_urlopen.assert_called_once()
            _, outbox = self.request("GET", "/api/v1/integrations/sms/outbox")
        self.assertEqual(outbox["messages"][0]["recipient_display"], "+••••••4567")
        self.assertIn("not retained", outbox["messages"][0]["body_preview"])
        self.assertEqual(outbox["messages"][0]["status"], "Sent")

    def test_idempotency_key_cannot_be_reused_for_different_content(self):
        env = {"STAMFORD_SMS_MODE": "mock"}
        with patch.dict(os.environ, env, clear=True):
            first = {"to": "+6591234567", "message": "First message", "idempotencyKey": "stable-key"}
            self.assertEqual(self.request("POST", "/api/v1/integrations/sms/send", first)[0], 201)
            second = {"to": "+6591234567", "message": "Different message", "idempotencyKey": "stable-key"}
            code, result = self.request("POST", "/api/v1/integrations/sms/send", second)
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "IDEMPOTENCY_KEY_REUSED")

    def test_mock_invitation_replaces_pass_url_with_explicit_demo_note(self):
        env = {"STAMFORD_SMS_MODE": "mock"}
        with patch.dict(os.environ, env, clear=True):
            code, result = self.request("POST", "/api/v1/integrations/sms/send", {
                "to": "+6591234567", "message": "Visit details: {PASS_URL}", "purpose": "invitation",
                "passToken": "A" * 32, "idempotencyKey": "mock-invitation-1"})
            self.assertEqual(code, 201)
            self.assertEqual(result["status"], "Simulated")
            self.assertIn("no SMS was sent", result["note"])
            code, outbox = self.request("GET", "/api/v1/integrations/sms/outbox")
        self.assertEqual(code, 200)
        preview = outbox["messages"][0]["body_preview"]
        self.assertIn("DEMO ONLY", preview)
        self.assertNotIn("{PASS_URL}", preview)
        self.assertNotIn("http://127.0.0.1", preview)

    def test_live_invitation_is_blocked_without_public_pass_page(self):
        env = {"STAMFORD_SMS_MODE": "vonage", "VONAGE_API_KEY": "test-key", "VONAGE_API_SECRET": "test-secret",
               "VONAGE_FROM": "TestVMS", "STAMFORD_SMS_ALLOWED_RECIPIENTS": "+6591234567"}
        with patch.dict(os.environ, env, clear=True), patch("urllib.request.urlopen") as mocked_urlopen:
            code, result = self.request("POST", "/api/v1/integrations/sms/send", {
                "to": "+6591234567", "message": "Secure pass: {PASS_URL}", "purpose": "invitation",
                "passToken": "A" * 32, "idempotencyKey": "live-invitation-no-host"})
        self.assertEqual(code, 503)
        self.assertEqual(result["error"], "PUBLIC_PASS_URL_NOT_CONFIGURED")
        mocked_urlopen.assert_not_called()

    def test_live_invitation_uses_hosted_https_template_and_active_token(self):
        env = {"STAMFORD_SMS_MODE": "vonage", "VONAGE_API_KEY": "test-key", "VONAGE_API_SECRET": "test-secret",
               "VONAGE_FROM": "TestVMS", "STAMFORD_SMS_ALLOWED_RECIPIENTS": "+6591234567",
               "STAMFORD_PUBLIC_PASS_URL_TEMPLATE": "https://vms.example.org/pass/{token}"}
        provider_response = MagicMock()
        provider_response.__enter__.return_value.read.return_value = json.dumps({"messages": [{"status": "0", "message-id": "provider-invite-1"}]}).encode()
        with patch.dict(os.environ, env, clear=True), patch("urllib.request.urlopen", return_value=provider_response) as mocked_urlopen:
            code, result = self.request("POST", "/api/v1/integrations/sms/send", {
                "to": "+6591234567", "message": "Secure pass: {PASS_URL}", "purpose": "invitation",
                "passToken": "OpaqueToken_0123456789abcdef0123456789", "idempotencyKey": "live-invitation-hosted"})
            self.assertEqual(code, 201)
            self.assertEqual(result["status"], "Sent")
            request = mocked_urlopen.call_args.args[0]
            parsed = urllib.parse.parse_qs(request.data.decode())
        self.assertIn("https://vms.example.org/pass/OpaqueToken_0123456789abcdef0123456789", parsed["text"][0])

    def test_invalid_public_pass_templates_are_rejected(self):
        for template in ("http://vms.example.org/pass/{token}", "https://localhost/pass/{token}",
                         "https://127.0.0.1/pass/{token}", "https://vms.example.org/pass/no-token"):
            with self.subTest(template=template):
                self.assertFalse(IntegrationHub._is_safe_pass_template(template))

    def test_live_sender_does_not_store_full_message_or_phone(self):
        hub = IntegrationHub(self.db)
        env = {"STAMFORD_SMS_MODE": "twilio", "TWILIO_ACCOUNT_SID": "AC-test", "TWILIO_AUTH_TOKEN": "secret", "TWILIO_FROM_NUMBER": "+15005550006", "STAMFORD_SMS_ALLOWED_RECIPIENTS": "+6591234567"}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"sid": "SM123"}).encode()
        with patch.dict(os.environ, env, clear=True), patch("urllib.request.urlopen", return_value=response):
            result = hub.send_sms({"to": "+6591234567", "message": "Sensitive visitor content", "idempotencyKey": "privacy-key"})
        with closing(hub._connect()) as conn:
            row = conn.execute("SELECT recipient_display,body_preview,body_sha256 FROM integration_sms_outbox WHERE message_id=?", (result["messageId"],)).fetchone()
        self.assertNotEqual(row["recipient_display"], "+6591234567")
        self.assertNotIn("Sensitive visitor content", row["body_preview"])
        self.assertEqual(len(row["body_sha256"]), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from service.vms_service import StateStore, make_handler


class VmsServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ui = self.root / "ui"
        self.ui.mkdir()
        (self.ui / "index.html").write_text("<!doctype html><title>Test UI</title>", encoding="utf-8")
        self.store = StateStore(self.root / "data" / "test.sqlite3")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ui, self.store, auth_enabled=False))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        merged = {"Content-Type": "application/json", **(headers or {})}
        req = Request(self.base + path, data=data, headers=merged, method=method)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def valid_state(self):
        return {"schemaVersion": 1, "visits": [{"id": "VMS-TEST-1", "status": "Invited"}], "cards": [], "groups": [], "events": [], "audit": [], "activity": [], "nextQrIndex": 19}

    def test_health_is_explicitly_prototype_only(self):
        code, body = self.request("GET", "/api/v1/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["boundToLoopback"])
        self.assertEqual(body["authentication"], "test-bypass")
        self.assertEqual(body["mode"], "prototype-only")
        self.assertEqual(body["build"], "5.5.0")

    def test_face_attendance_feature_is_gated_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            code, status = self.request("GET", "/api/v1/face-attendance/status")
            self.assertEqual(code, 200)
            self.assertFalse(status["studentFaceAttendanceEnabled"])
            self.assertFalse(status["groupFaceIdentificationEnabled"])
            self.assertTrue(status["recognitionResultRequiresHumanConfirmation"])
            code, result = self.request("GET", "/api/v1/face-attendance/students")
            self.assertEqual(code, 403)
            self.assertEqual(result["error"], "FACE_ATTENDANCE_DISABLED")

    def test_state_round_trip_and_revision(self):
        code, initial = self.request("GET", "/api/v1/state")
        self.assertEqual(code, 200)
        self.assertFalse(initial["hasState"])
        code, saved = self.request("PUT", "/api/v1/state", {"expectedRevision": 0, "state": self.valid_state()})
        self.assertEqual(code, 200)
        self.assertEqual(saved["revision"], 1)
        code, loaded = self.request("GET", "/api/v1/state")
        self.assertEqual(code, 200)
        self.assertEqual(loaded["revision"], 1)
        self.assertEqual(loaded["state"]["visits"][0]["id"], "VMS-TEST-1")

    def test_stale_revision_is_rejected_instead_of_overwriting(self):
        state = self.valid_state()
        state["visits"][0]["name"] = "Version A"
        self.assertEqual(self.request("PUT", "/api/v1/state", {"expectedRevision": 0, "state": state})[0], 200)
        state["visits"][0]["name"] = "Stale Version B"
        code, result = self.request("PUT", "/api/v1/state", {"expectedRevision": 0, "state": state})
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "STATE_REVISION_CONFLICT")
        loaded = self.request("GET", "/api/v1/state")[1]
        self.assertEqual(loaded["state"]["visits"][0]["name"], "Version A")

    def test_invalid_schema_is_rejected(self):
        code, result = self.request("PUT", "/api/v1/state", {"expectedRevision": 0, "state": {"schemaVersion": 999}})
        self.assertEqual(code, 400)
        self.assertEqual(result["error"], "INVALID_STATE")

    def test_cross_origin_write_is_rejected(self):
        code, result = self.request("PUT", "/api/v1/state", {"expectedRevision": 0, "state": self.valid_state()}, {"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertEqual(result["error"], "CROSS_ORIGIN_REJECTED")

    def test_static_ui_served(self):
        req = Request(self.base + "/")
        with urlopen(req, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("Test UI", response.read().decode("utf-8"))
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
            self.assertEqual(response.headers.get("Pragma"), "no-cache")


    def test_kiosk_face_status_defaults_to_explicit_mock_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            code, body = self.request("GET", "/api/v1/kiosk-face/status")
        self.assertEqual(code, 200)
        self.assertEqual(body["mode"], "mock")
        self.assertTrue(body["mockAvailable"])
        self.assertFalse(body["liveReady"])
        self.assertIn("approved profiles", body["liveGalleryBoundary"])

    def test_kiosk_face_mock_api_records_only_after_explicit_confirmation(self):
        with patch.dict(os.environ, {"STAMFORD_KIOSK_FACE_MODE": "mock"}, clear=True):
            code, match = self.request("POST", "/api/v1/kiosk-face/match", {
                "imageData": "data:image/jpeg;base64,aGVsbG8=", "personType": "Contractor",
                "eventType": "CheckIn", "sessionId": "ATT-HTTP-KIOSK",
            })
            self.assertEqual(code, 200)
            self.assertTrue(match["simulated"])
            self.assertEqual(match["candidate"]["personType"], "Contractor")
            code, denied = self.request("POST", "/api/v1/kiosk-face/confirm", {
                "ticketId": match["ticketId"], "sessionId": "ATT-HTTP-KIOSK", "confirmMatch": False,
            })
            self.assertEqual(code, 400)
            self.assertEqual(denied["error"], "HUMAN_CONFIRMATION_REQUIRED")
            code, result = self.request("POST", "/api/v1/kiosk-face/confirm", {
                "ticketId": match["ticketId"], "sessionId": "ATT-HTTP-KIOSK", "confirmMatch": True,
            })
            self.assertEqual(code, 201)
            self.assertTrue(result["simulated"])
            self.assertFalse(result["writtenToGenetec"])
            code, events = self.request("GET", "/api/v1/kiosk-face/events?sessionId=ATT-HTTP-KIOSK")
            self.assertEqual(code, 200)
            self.assertEqual(events["count"], 1)
            self.assertEqual(events["events"][0]["personType"], "Contractor")

    def test_kiosk_live_api_does_not_silently_use_mock_when_gate_closed(self):
        with patch.dict(os.environ, {"STAMFORD_KIOSK_FACE_MODE": "live"}, clear=True):
            code, result = self.request("POST", "/api/v1/kiosk-face/match", {
                "imageData": "data:image/jpeg;base64,aGVsbG8=", "personType": "Student",
                "eventType": "CheckIn", "sessionId": "ATT-HTTP-LIVE-GATE",
            })
        self.assertEqual(code, 403)
        self.assertEqual(result["error"], "FACE_ATTENDANCE_DISABLED")
        self.assertNotIn("simulated", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)

import base64
import hashlib
from io import BytesIO
import importlib.util
from contextlib import closing
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from service.domain import DomainStore, SCHOOL_TIMEZONE
from service.vms_service import StateStore, make_handler


class DomainApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ui = self.root / "ui"
        self.ui.mkdir()
        (self.ui / "index.html").write_text("<!doctype html><title>Test UI</title>", encoding="utf-8")
        db = self.root / "data" / "test.sqlite3"
        self.state_store = StateStore(db)
        self.domain = DomainStore(db)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ui, self.state_store, self.domain))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.payload = {
            "id": "VMS-TEST-SECURE-01",
            "name": "Synthetic Visitor",
            "email": "visitor@example.test",
            "host": "Synthetic Host",
            "destination": "Main Reception",
            "visitDate": datetime.now(SCHOOL_TIMEZONE).date().isoformat(),
            "window": "00:00–23:59",
            "purpose": "Automated test only",
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None, raw=False):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(self.base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})}, method=method)
        try:
            with urlopen(req, timeout=3) as response:
                content = response.read()
                return response.status, content if raw else json.loads(content.decode("utf-8"))
        except HTTPError as exc:
            content = exc.read()
            return exc.code, content if raw else json.loads(content.decode("utf-8"))

    def create(self):
        code, result = self.request("POST", "/api/v1/visits", self.payload)
        self.assertEqual(code, 201, result)
        self.assertTrue(result["ok"])
        return result

    def test_create_returns_random_token_but_database_stores_only_digest(self):
        result = self.create()
        token = result["qrToken"]
        self.assertGreaterEqual(len(token), 40)
        self.assertNotIn("qrToken", result["visit"])
        with closing(sqlite3.connect(self.state_store.db_path)) as conn:
            row = conn.execute("SELECT qr_token_hash FROM domain_visits WHERE booking_id=?", (self.payload["id"],)).fetchone()
        self.assertEqual(row[0], hashlib.sha256(token.encode("ascii")).hexdigest())
        self.assertNotEqual(row[0], token)
        raw_db = self.state_store.db_path.read_bytes()
        self.assertNotIn(token.encode("ascii"), raw_db)

    @unittest.skipUnless(importlib.util.find_spec("cv2") and importlib.util.find_spec("numpy"), "Optional OpenCV scanner dependency is unavailable")
    def test_local_camera_qr_frame_endpoint_decodes_token_without_persisting_frame(self):
        # Importing the VMS QR helper adds the bundled QR encoder to sys.path.
        from service.qr_code import make_svg  # noqa: F401
        import qrcode
        from PIL import Image, ImageDraw
        token = "VMS-LOCAL-CAMERA-SCAN-TOKEN-0123456789ABCDEF"
        qr = qrcode.QRCode(box_size=1, border=4)
        qr.add_data(token)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        scale = 8
        image = Image.new("RGB", (len(matrix) * scale, len(matrix) * scale), "white")
        draw = ImageDraw.Draw(image)
        for y, row in enumerate(matrix):
            for x, module in enumerate(row):
                if module:
                    draw.rectangle((x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1), fill="black")
        output = BytesIO()
        image.save(output, format="JPEG", quality=96)
        data_url = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        code, result = self.request("POST", "/api/v1/qr/decode-frame", {"imageData": data_url})
        self.assertEqual(code, 200, result)
        self.assertTrue(result["detected"])
        self.assertEqual(result["qrText"], token)

    def test_secure_qr_svg_generated_and_token_not_embedded_as_plain_text(self):
        token = self.create()["qrToken"]
        code, svg = self.request("POST", "/api/v1/qr/svg", {"qrToken": token}, raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"<svg", svg[:300])
        self.assertNotIn(token.encode("ascii"), svg)

    def test_checkin_checkout_and_exact_retries_are_idempotent(self):
        token = self.create()["qrToken"]
        code, validation = self.request("POST", "/api/v1/qr/validate", {"qrToken": token})
        self.assertEqual(code, 200)
        self.assertTrue(validation["canCheckIn"])
        checkin_body = {"qrToken": token, "eventId": "VMS-TEST-SECURE-01-CHECKIN"}
        code, checkin = self.request("POST", "/api/v1/qr/check-in", checkin_body)
        self.assertEqual(code, 200, checkin)
        self.assertEqual(checkin["status"], "Inside")
        code, checkin_retry = self.request("POST", "/api/v1/qr/check-in", checkin_body)
        self.assertEqual(code, 200)
        self.assertTrue(checkin_retry["alreadyApplied"])
        self.assertEqual(checkin_retry["eventId"], checkin["eventId"])
        checkout_body = {"qrToken": token, "eventId": "VMS-TEST-SECURE-01-CHECKOUT"}
        code, checkout = self.request("POST", "/api/v1/qr/check-out", checkout_body)
        self.assertEqual(code, 200, checkout)
        self.assertEqual(checkout["status"], "Checked out")
        code, checkout_retry = self.request("POST", "/api/v1/qr/check-out", checkout_body)
        self.assertEqual(code, 200)
        self.assertTrue(checkout_retry["alreadyApplied"])

    def test_second_checkin_with_different_event_is_rejected(self):
        token = self.create()["qrToken"]
        self.assertEqual(self.request("POST", "/api/v1/qr/check-in", {"qrToken": token, "eventId": "VMS-TEST-SECURE-01-CHECKIN"})[0], 200)
        code, result = self.request("POST", "/api/v1/qr/check-in", {"qrToken": token, "eventId": "VMS-TEST-SECURE-01-CHECKIN2"})
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "CHECKIN_STATE_INVALID")

    def test_rotation_invalidates_old_token_and_does_not_expose_token_in_list(self):
        original = self.create()["qrToken"]
        code, rotated = self.request("POST", f"/api/v1/visits/{self.payload['id']}/qr/rotate", {})
        self.assertEqual(code, 200, rotated)
        self.assertNotEqual(original, rotated["qrToken"])
        code, old_result = self.request("POST", "/api/v1/qr/validate", {"qrToken": original})
        self.assertEqual(code, 404)
        self.assertEqual(old_result["error"], "QR_NOT_RECOGNISED")
        code, listed = self.request("GET", "/api/v1/visits")
        self.assertEqual(code, 200)
        self.assertNotIn("qrToken", listed["visits"][0])

    def test_cancel_revokes_qr_token(self):
        token = self.create()["qrToken"]
        code, cancelled = self.request("POST", f"/api/v1/visits/{self.payload['id']}/cancel", {})
        self.assertEqual(code, 200, cancelled)
        self.assertTrue(cancelled["tokenRevoked"])
        self.assertEqual(cancelled["visit"]["status"], "Cancelled")
        code, result = self.request("POST", "/api/v1/qr/validate", {"qrToken": token})
        self.assertEqual(code, 404)

    def test_visit_window_unicode_dash_and_invisible_format_chars_are_normalized(self):
        payload = dict(
            self.payload,
            id="VMS-TEST-UNICODE-01",
            visitDate=(datetime.now(SCHOOL_TIMEZONE).date() + timedelta(days=1)).isoformat(),
            # Full-width digits/colon, Unicode minus and a zero-width space reproduce the class of UI formatting issue.
            window="０９：００−\u200b１０：００",
        )
        code, result = self.request("POST", "/api/v1/visits", payload)
        self.assertEqual(code, 201, result)
        self.assertEqual(result["visit"]["window"], "09:00–10:00")

    def test_visit_window_rejects_invalid_clock_values(self):
        payload = dict(
            self.payload,
            id="VMS-TEST-BAD-CLOCK-01",
            visitDate=(datetime.now(SCHOOL_TIMEZONE).date() + timedelta(days=1)).isoformat(),
            window="29:00−30:00",
        )
        code, result = self.request("POST", "/api/v1/visits", payload)
        self.assertEqual(code, 400)
        self.assertEqual(result["error"], "INVALID_VISIT_WINDOW")

    def test_visit_window_is_enforced(self):
        future = dict(self.payload, id="VMS-TEST-FUTURE-01", visitDate=(datetime.now(SCHOOL_TIMEZONE).date() + timedelta(days=1)).isoformat(), window="13:00–14:00")
        code, created = self.request("POST", "/api/v1/visits", future)
        self.assertEqual(code, 201, created)
        code, result = self.request("POST", "/api/v1/qr/check-in", {"qrToken": created["qrToken"], "eventId": "VMS-TEST-FUTURE-01-CHECKIN"})
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "VISIT_WINDOW_CLOSED")
        self.assertEqual(result["visitDate"], future["visitDate"])
        self.assertEqual(result["visitWindow"], future["window"])
        self.assertIn("checkInOpensAtLocal", result)
        self.assertIn("checkInClosesAtLocal", result)

    def test_amend_schedule_preserves_live_qr_and_accepts_checkin_at_new_window(self):
        created = self.create()
        token = created["qrToken"]
        today = datetime.now(SCHOOL_TIMEZONE).date()
        tomorrow = today + timedelta(days=1)
        pre_amend_now = datetime.combine(today, datetime.strptime("12:00", "%H:%M").time(), tzinfo=SCHOOL_TIMEZONE).astimezone(timezone.utc)
        with patch("service.domain.utc_now", return_value=pre_amend_now):
            code, amended = self.request("POST", f"/api/v1/visits/{self.payload['id']}/amend", {"visitDate": tomorrow.isoformat(), "window": "12:31–13:31"})
        self.assertEqual(code, 200, amended)
        self.assertTrue(amended["qrTokenPreserved"])
        self.assertEqual(amended["visit"]["visitDate"], tomorrow.isoformat())
        self.assertEqual(amended["visit"]["window"], "12:31–13:31")
        checkin_now = datetime.combine(tomorrow, datetime.strptime("12:40", "%H:%M").time(), tzinfo=SCHOOL_TIMEZONE).astimezone(timezone.utc)
        with patch("service.domain.utc_now", return_value=checkin_now):
            code, validation = self.request("POST", "/api/v1/qr/validate", {"qrToken": token})
            self.assertEqual(code, 200, validation)
            self.assertTrue(validation["canCheckIn"], validation)
            code, checked_in = self.request("POST", "/api/v1/qr/check-in", {"qrToken": token, "eventId": "VMS-TEST-AMEND-SCHEDULE-CHECKIN"})
            self.assertEqual(code, 200, checked_in)
            self.assertEqual(checked_in["status"], "Inside")

    def test_schedule_amendment_is_not_allowed_after_checkin(self):
        token = self.create()["qrToken"]
        code, checked_in = self.request("POST", "/api/v1/qr/check-in", {"qrToken": token, "eventId": "VMS-TEST-AMEND-AFTER-CHECKIN"})
        self.assertEqual(code, 200, checked_in)
        future_date = (datetime.now(SCHOOL_TIMEZONE).date() + timedelta(days=1)).isoformat()
        code, result = self.request("POST", f"/api/v1/visits/{self.payload['id']}/amend", {"visitDate": future_date, "window": "12:00–13:00"})
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "AMEND_NOT_ALLOWED")

    def test_visit_window_accepts_arbitrary_minute_values(self):
        payload = dict(self.payload, id="VMS-TEST-ANY-MINUTE-01", window="10:07–10:38")
        code, result = self.request("POST", "/api/v1/visits", payload)
        self.assertEqual(code, 201, result)
        self.assertEqual(result["visit"]["window"], "10:07–10:38")

    def test_checkin_inside_school_local_visit_window_is_accepted(self):
        # A fixed school-local clock proves the decision independent of the
        # machine/container timezone that runs this test suite.
        school_now = datetime(2026, 10, 17, 10, 30, tzinfo=SCHOOL_TIMEZONE)
        now_utc = school_now.astimezone(timezone.utc)
        payload = dict(self.payload, id="VMS-TEST-SCHOOL-TIME-01",
                       visitDate="2026-10-17", window="10:15–10:45")
        with patch("service.domain.utc_now", return_value=now_utc):
            code, created = self.request("POST", "/api/v1/visits", payload)
            self.assertEqual(code, 201, created)
            token = created["qrToken"]
            code, validation = self.request("POST", "/api/v1/qr/validate", {"qrToken": token})
            self.assertEqual(code, 200, validation)
            self.assertTrue(validation["canCheckIn"], validation)
            self.assertEqual(validation["schoolTimeZone"], "Asia/Singapore")
            self.assertEqual(validation["schoolTimeNow"], "2026-10-17T10:30+08:00")
            code, checked_in = self.request("POST", "/api/v1/qr/check-in",
                {"qrToken": token, "eventId": "VMS-TEST-SCHOOL-TIME-01-CHECKIN"})
            self.assertEqual(code, 200, checked_in)
            self.assertEqual(checked_in["status"], "Inside")

    def test_validation_returns_school_timezone_window_diagnostics(self):
        school_now = datetime(2026, 10, 17, 10, 30, tzinfo=SCHOOL_TIMEZONE)
        with patch("service.domain.utc_now", return_value=school_now.astimezone(timezone.utc)):
            payload = dict(self.payload, id="VMS-TEST-TIME-DIAGNOSTICS-01",
                           visitDate="2026-10-17", window="10:15–10:45")
            code, created = self.request("POST", "/api/v1/visits", payload)
            self.assertEqual(code, 201, created)
            code, validation = self.request("POST", "/api/v1/qr/validate", {"qrToken": created["qrToken"]})
            self.assertEqual(code, 200, validation)
            self.assertEqual(validation["visitStartLocal"], "2026-10-17T10:15+08:00")
            self.assertEqual(validation["visitEndLocal"], "2026-10-17T10:45+08:00")
            self.assertEqual(validation["checkInOpensAtLocal"], "2026-10-17T09:45+08:00")
            self.assertEqual(validation["checkInClosesAtLocal"], "2026-10-17T11:45+08:00")

    def test_duplicate_booking_id_does_not_duplicate_visit(self):
        self.create()
        code, result = self.request("POST", "/api/v1/visits", self.payload)
        self.assertEqual(code, 409)
        self.assertEqual(result["error"], "BOOKING_ID_EXISTS")
        self.assertEqual(len(self.domain.list_visits()), 1)

    def test_bad_qr_and_event_id_fail_closed(self):
        code, bad = self.request("POST", "/api/v1/qr/validate", {"qrToken": "QR-DEMO-0001"})
        self.assertEqual(code, 400)
        self.assertEqual(bad["error"], "INVALID_QR_TOKEN")
        token = self.create()["qrToken"]
        code, bad_event = self.request("POST", "/api/v1/qr/check-in", {"qrToken": token, "eventId": "tiny"})
        self.assertEqual(code, 400)
        self.assertEqual(bad_event["error"], "INVALID_EVENT_ID")


if __name__ == "__main__":
    unittest.main(verbosity=2)

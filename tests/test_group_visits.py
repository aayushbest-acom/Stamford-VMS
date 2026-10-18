"""Tests for persistent group bookings, roster scoping, captures and batch attendance."""
import base64
import json
import os
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from service.group_visits import GroupVisitError, GroupVisitStore
from service.vms_service import StateStore, make_handler
from service.domain import DomainStore
from service.face_attendance import FaceAttendanceStore


def image_data(raw=b"synthetic-demo-image"):
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def booking_payload(group_id="GR-TEST001"):
    return {
        "groupEventId": group_id,
        "organizationName": "Demo Visiting School",
        "organiserName": "Taylor Coach",
        "hostName": "Morgan Lee",
        "destination": "Science Centre",
        "purpose": "Robotics exchange visit",
        "startsAtLocal": "2026-10-18T09:30",
        "endsAtLocal": "2026-10-18T12:00",
        "rosterMembers": [
            {"personType": "Student", "displayName": "Ava Reed", "externalId": "STU-DEMO-01", "affiliation": "Grade 6A"},
            {"personType": "Guardian", "displayName": "Olivia Reed", "externalId": "GDN-DEMO-01", "affiliation": "Parent", "guardianOf": "Ava Reed", "contactHint": "ending 1122"},
            {"personType": "Other", "displayName": "Visiting Interpreter", "affiliation": "Interpreter"},
        ],
    }


class GroupVisitStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "face.sqlite3"
        self.store = GroupVisitStore(self.path, protector=lambda raw, desc: b"encrypted:" + raw, unprotector=lambda raw: raw.removeprefix(b"encrypted:"))

    def tearDown(self):
        self.temp.cleanup()

    def test_create_group_with_students_guardians_and_other_details(self):
        group = self.store.create_booking(booking_payload())
        self.assertEqual(group["expectedCount"], 3)
        self.assertEqual([x["personType"] for x in group["roster"]], ["Student", "Guardian", "Other"])
        self.assertEqual(group["roster"][1]["guardianOf"], "Ava Reed")
        self.assertEqual(group["status"], "Expected")

    def test_create_rejects_invalid_window_duplicate_ids_and_oversize_roster(self):
        payload = booking_payload()
        payload["endsAtLocal"] = payload["startsAtLocal"]
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.create_booking(payload)
        self.assertEqual(ctx.exception.code, "INVALID_VISIT_WINDOW")
        self.store.create_booking(booking_payload())
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.create_booking(booking_payload())
        self.assertEqual(ctx.exception.code, "GROUP_BOOKING_ALREADY_EXISTS")
        payload = booking_payload("GR-TEST002")
        payload["rosterMembers"] = payload["rosterMembers"] * 40
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.create_booking(payload)
        self.assertEqual(ctx.exception.code, "INVALID_ROSTER")

    def test_group_qr_is_opaque_hash_only_and_rotation_revokes_old_pass(self):
        payload = booking_payload("GR-QRTEST1")
        payload["startsAtLocal"] = "2026-10-19T09:30"
        payload["endsAtLocal"] = "2026-10-19T12:00"
        created = self.store.create_booking(payload)
        raw_token = created["groupQrToken"]
        self.assertNotIn("groupQrToken", self.store.get_booking("GR-QRTEST1"))
        self.assertTrue(self.store.get_booking("GR-QRTEST1")["qrPassAvailable"])
        verified = self.store.validate_qr(raw_token)
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["groupEventId"], "GR-QRTEST1")
        self.assertFalse(verified["groupQrAuthorisesAttendance"])
        with closing(self.store._connect()) as conn:
            saved = conn.execute("SELECT token_hash FROM group_qr_tokens WHERE group_event_id=?", ("GR-QRTEST1",)).fetchone()[0]
        self.assertNotEqual(saved, raw_token)
        rotated = self.store.rotate_qr("GR-QRTEST1")
        self.assertNotEqual(rotated["groupQrToken"], raw_token)
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.validate_qr(raw_token)
        self.assertEqual(ctx.exception.code, "GROUP_QR_REVOKED")
        self.assertTrue(self.store.validate_qr(rotated["groupQrToken"])["valid"])

    def test_reconciliation_requires_full_roster_and_cannot_mark_attendee_absent_after_checkin(self):
        self.store.create_booking(booking_payload())
        with patch.dict(os.environ, {"STAMFORD_GROUP_VISIT_MODE": "mock"}, clear=True):
            scan = self.store.create_scan("GR-TEST001", {"eventType": "CheckIn", "imageData": image_data(), "imageWidth": 640, "imageHeight": 480}, mode="mock")
        candidates = self.store.mock_match_result("GR-TEST001", scan, 640, 480)["candidates"]
        self.store.update_scan_match(scan["scanId"], {"candidates": candidates, "simulated": True, "mode": "mock"})
        self.store.confirm_mock_scan("GR-TEST001", {"scanId": scan["scanId"], "eventType": "CheckIn", "operator": "Test Operator", "confirmBatch": True, "confirmations": [{"ticketId": candidates[0]["ticketId"], "memberId": candidates[0]["memberId"], "confirmMatch": True}]})
        roster = self.store.get_booking("GR-TEST001")["roster"]
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.reconcile("GR-TEST001", {"operator": "Test Operator", "reason": "partial review", "decisions": [{"memberId": roster[0]["memberId"], "disposition": "CheckedIn"}]})
        self.assertEqual(ctx.exception.code, "ROSTER_RECONCILIATION_INCOMPLETE")
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.reconcile("GR-TEST001", {"operator": "Test Operator", "reason": "reviewed", "decisions": [{"memberId": roster[0]["memberId"], "disposition": "Absent"}, {"memberId": roster[1]["memberId"], "disposition": "Unresolved"}, {"memberId": roster[2]["memberId"], "disposition": "Unresolved"}]})
        self.assertEqual(ctx.exception.code, "ATTENDANCE_ALREADY_RECORDED")

    def test_status_defaults_to_mock_and_reports_biometric_enrolment_disabled_for_group_photos(self):
        with patch.dict(os.environ, {}, clear=True):
            status = self.store.status()
        self.assertEqual(status["mode"], "mock")
        self.assertFalse(status["liveFaceMatchingApprovalFlag"])
        self.assertFalse(status["groupPhotoUsedForEnrollment"])
        self.assertTrue(status["humanConfirmationRequired"])
        self.assertEqual(status["photoRetention"]["status"], "preview-only")

    def test_confirmed_scan_is_readable_after_review_ttl_but_unconfirmed_scan_expires(self):
        self.store.create_booking(booking_payload())
        with patch.dict(os.environ, {"STAMFORD_GROUP_VISIT_MODE": "mock"}, clear=True):
            scan = self.store.create_scan("GR-TEST001", {"eventType": "CheckIn", "imageData": image_data(), "imageWidth": 640, "imageHeight": 480}, mode="mock")
        with closing(self.store._connect()) as conn:
            conn.execute("UPDATE group_visit_scans SET expires_at_utc='2000-01-01T00:00:00.000Z',scan_status='Confirmed' WHERE scan_id=?", (scan["scanId"],))
        self.assertEqual(self.store.get_scan("GR-TEST001", scan["scanId"])["scanStatus"], "Confirmed")
        with closing(self.store._connect()) as conn:
            conn.execute("UPDATE group_visit_scans SET scan_status='Captured' WHERE scan_id=?", (scan["scanId"],))
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.get_scan("GR-TEST001", scan["scanId"])
        self.assertEqual(ctx.exception.code, "GROUP_SCAN_EXPIRED")

    def test_photo_not_persisted_without_explicit_retention_approval(self):
        self.store.create_booking(booking_payload())
        with patch.dict(os.environ, {"STAMFORD_GROUP_VISIT_MODE": "mock"}, clear=True):
            scan = self.store.create_scan("GR-TEST001", {"eventType": "CheckIn", "imageData": image_data(), "imageWidth": 640, "imageHeight": 480}, mode="mock")
        self.assertFalse(scan["photoRetained"])
        self.assertIn("preview-only", scan["photoRetentionStatus"])
        with closing(self.store._connect()) as conn:
            row = conn.execute("SELECT photo_protected,image_sha256 FROM group_visit_scans WHERE scan_id=?", (scan["scanId"],)).fetchone()
        self.assertIsNone(row["photo_protected"])
        self.assertEqual(len(row["image_sha256"]), 64)

    def test_approved_photo_retention_encrypts_bytes_and_applies_expiry(self):
        self.store.create_booking(booking_payload())
        with patch.dict(os.environ, {"STAMFORD_GROUP_PHOTO_RETENTION_APPROVED": "1", "STAMFORD_GROUP_PHOTO_RETENTION_DAYS": "21"}, clear=True):
            scan = self.store.create_scan("GR-TEST001", {"eventType": "CheckIn", "imageData": image_data(b"private-photo"), "imageWidth": 640, "imageHeight": 480}, mode="mock")
        self.assertTrue(scan["photoRetained"])
        self.assertEqual(scan["photoRetentionStatus"], "encrypted-retention-enabled")
        with closing(self.store._connect()) as conn:
            row = conn.execute("SELECT photo_protected,photo_retention_expires_at_utc FROM group_visit_scans WHERE scan_id=?", (scan["scanId"],)).fetchone()
        self.assertEqual(row["photo_protected"], b"encrypted:private-photo")
        self.assertIsNotNone(row["photo_retention_expires_at_utc"])

    def test_mock_group_checkin_checkout_is_collective_and_idempotent(self):
        self.store.create_booking(booking_payload())
        with patch.dict(os.environ, {"STAMFORD_GROUP_VISIT_MODE": "mock"}, clear=True):
            checkin_meta = self.store.create_scan("GR-TEST001", {"eventType": "CheckIn", "imageData": image_data(), "imageWidth": 640, "imageHeight": 480}, mode="mock")
            checkin_match = self.store.mock_match_result("GR-TEST001", checkin_meta, 640, 480)
            self.store.update_scan_match(checkin_meta["scanId"], checkin_match)
            selected = checkin_match["candidates"][:2]
            checkin = self.store.confirm_mock_scan("GR-TEST001", {"scanId": checkin_meta["scanId"], "eventType": "CheckIn", "operator": "Test Operator", "confirmBatch": True, "confirmations": [{"ticketId": x["ticketId"], "memberId": x["memberId"], "confirmMatch": True} for x in selected]})
            self.assertEqual(checkin["recordedCount"], 2)
            self.assertTrue(checkin["simulated"])
            booking = self.store.get_booking("GR-TEST001")
            self.assertTrue(booking["arrivalAtUtc"])
            self.assertEqual(sum(1 for x in booking["roster"] if x["attendanceState"] == "CheckedIn"), 2)
            with self.assertRaises(GroupVisitError) as ctx:
                self.store.confirm_mock_scan("GR-TEST001", {"scanId": checkin_meta["scanId"], "eventType": "CheckIn", "operator": "Test Operator", "confirmBatch": True, "confirmations": [{"ticketId": selected[0]["ticketId"], "memberId": selected[0]["memberId"], "confirmMatch": True}]})
            self.assertEqual(ctx.exception.code, "GROUP_SCAN_ALREADY_FINALISED")
            checkout_meta = self.store.create_scan("GR-TEST001", {"eventType": "CheckOut", "imageData": image_data(), "imageWidth": 640, "imageHeight": 480}, mode="mock")
            checkout_match = self.store.mock_match_result("GR-TEST001", checkout_meta, 640, 480)
            self.store.update_scan_match(checkout_meta["scanId"], checkout_match)
            checkout_members = {x["memberId"]: x for x in checkout_match["candidates"]}
            # Only people actually checked in are selected for face checkout.
            checkout = self.store.confirm_mock_scan("GR-TEST001", {"scanId": checkout_meta["scanId"], "eventType": "CheckOut", "operator": "Test Operator", "confirmBatch": True, "confirmations": [{"ticketId": checkout_members[x["memberId"]]["ticketId"], "memberId": x["memberId"], "confirmMatch": True} for x in selected]})
            self.assertEqual(checkout["recordedCount"], 2)
            self.assertEqual(self.store.get_booking("GR-TEST001")["status"], "Departed — reconciliation required")

    def test_checkout_cannot_be_captured_before_group_arrival(self):
        self.store.create_booking(booking_payload())
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.create_scan("GR-TEST001", {"eventType": "CheckOut", "imageData": image_data(), "imageWidth": 640, "imageHeight": 480}, mode="mock")
        self.assertEqual(ctx.exception.code, "GROUP_CHECKIN_REQUIRED")

    def test_roster_locked_after_arrival_and_manual_reconciliation_requires_reason(self):
        self.store.create_booking(booking_payload())
        with patch.dict(os.environ, {"STAMFORD_GROUP_VISIT_MODE": "mock"}, clear=True):
            self.store.create_scan("GR-TEST001", {"eventType": "CheckIn", "imageData": image_data(), "imageWidth": 640, "imageHeight": 480}, mode="mock")
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.add_roster("GR-TEST001", {"rosterMembers": [{"personType": "Guardian", "displayName": "Late Guardian"}]})
        self.assertEqual(ctx.exception.code, "ROSTER_LOCKED")
        group = self.store.get_booking("GR-TEST001")
        with self.assertRaises(GroupVisitError) as ctx:
            self.store.reconcile("GR-TEST001", {"operator": "Test Operator", "reason": "", "decisions": [{"memberId": group["roster"][0]["memberId"], "disposition": "CheckedIn"}]})
        self.assertEqual(ctx.exception.code, "FIELD_REQUIRED")


class GroupVisitApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        ui = self.root / "ui"
        ui.mkdir()
        (ui / "index.html").write_text("<title>group test</title>", encoding="utf-8")
        state = StateStore(self.root / "data" / "state.sqlite3")
        domain = DomainStore(state.db_path)
        face = FaceAttendanceStore(self.root / "data" / "face-attendance.sqlite3", protector=lambda raw, desc: b"enc:" + raw, unprotector=lambda raw: raw.removeprefix(b"enc:"))
        groups = GroupVisitStore(face.db_path, protector=lambda raw, desc: b"enc:" + raw, unprotector=lambda raw: raw.removeprefix(b"enc:"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ui, state, domain, face, group_store=groups))
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
        raw = None if body is None else json.dumps(body).encode()
        req = Request(self.base + path, data=raw, headers={"Content-Type": "application/json"}, method=method)
        try:
            with urlopen(req, timeout=4) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_group_status_api_defaults_to_safe_mock_and_preview_only(self):
        with patch.dict(os.environ, {}, clear=True):
            code, status = self.request("GET", "/api/v1/group-visits/status")
        self.assertEqual(code, 200)
        self.assertEqual(status["mode"], "mock")
        self.assertFalse(status["liveFaceMatchingApprovalFlag"])
        self.assertFalse(status["groupPhotoUsedForEnrollment"])
        self.assertEqual(status["photoRetention"]["status"], "preview-only")

    def test_group_booking_scan_and_confirm_routes(self):
        with patch.dict(os.environ, {"STAMFORD_GROUP_VISIT_MODE": "mock"}, clear=True):
            code, created = self.request("POST", "/api/v1/group-visits", booking_payload())
            self.assertEqual(code, 201)
            group = created["group"]
            self.assertEqual(group["expectedCount"], 3)
            code, scan = self.request("POST", "/api/v1/group-visits/GR-TEST001/scan", {"eventType": "CheckIn", "imageData": image_data(), "imageWidth": 640, "imageHeight": 480, "operator": "Test Operator"})
            self.assertEqual(code, 200)
            self.assertTrue(scan["simulated"])
            self.assertFalse(scan["groupImageUsedForEnrollment"])
            self.assertEqual(len(scan["candidates"]), 3)
            selected = scan["candidates"][:2]
            code, confirmed = self.request("POST", "/api/v1/group-visits/GR-TEST001/confirm", {"scanId": scan["scanId"], "eventType": "CheckIn", "operator": "Test Operator", "confirmBatch": True, "confirmations": [{"ticketId": x["ticketId"], "memberId": x["memberId"], "confirmMatch": True} for x in selected]})
            self.assertEqual(code, 201)
            self.assertEqual(confirmed["recordedCount"], 2)
            self.assertFalse(confirmed["writtenToGenetec"])
            code, loaded = self.request("GET", "/api/v1/group-visits/GR-TEST001")
            self.assertEqual(code, 200)
            self.assertEqual(loaded["group"]["checkedInCount"], 2)
            self.assertIn("arrivalAtUtc", loaded["group"])

    def test_group_qr_api_issues_validates_rotates_and_renders_svg(self):
        payload = booking_payload("GR-APIQR001")
        payload["startsAtLocal"] = "2026-10-19T09:30"
        payload["endsAtLocal"] = "2026-10-19T12:00"
        code, created = self.request("POST", "/api/v1/group-visits", payload)
        self.assertEqual(code, 201)
        token = created["group"]["groupQrToken"]
        code, validated = self.request("POST", "/api/v1/group-visits/qr/validate", {"groupQrToken": token})
        self.assertEqual(code, 200)
        self.assertFalse(validated["groupQrAuthorisesAttendance"])
        req = Request(self.base + "/api/v1/group-visits/qr/svg", data=json.dumps({"groupQrToken": token}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=4) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("image/svg+xml", response.headers.get("Content-Type", ""))
            self.assertIn(b"<svg", response.read())
        code, rotated = self.request("POST", "/api/v1/group-visits/GR-APIQR001/qr/rotate", {})
        self.assertEqual(code, 200)
        self.assertNotEqual(token, rotated["groupQrToken"])
        code, rejected = self.request("POST", "/api/v1/group-visits/qr/validate", {"groupQrToken": token})
        self.assertEqual(code, 410)
        self.assertEqual(rejected["error"], "GROUP_QR_REVOKED")

    def test_api_rejects_unknown_group_and_invalid_image_dimensions(self):
        code, result = self.request("POST", "/api/v1/group-visits/GR-MISSING/scan", {"eventType": "CheckIn", "imageData": image_data(), "imageWidth": 12, "imageHeight": 12})
        self.assertEqual(code, 404)
        self.assertEqual(result["error"], "GROUP_NOT_FOUND")


if __name__ == "__main__":
    unittest.main(verbosity=2)

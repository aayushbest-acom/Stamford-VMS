from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile

from service.domain import DomainStore, SCHOOL_TIMEZONE
from service.face_attendance import FaceAttendanceStore
from service.group_visits import GroupVisitStore
from service.integrations import IntegrationHub
from service.reporting import ReportService, REPORT_DEFINITIONS, ReportingError
from service.vms_service import StateStore, make_handler

ROOT = Path(__file__).resolve().parents[1]


class ReportServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.main_db = root / "prototype-state.sqlite3"
        self.face_db = root / "face-attendance.sqlite3"
        self.domain = DomainStore(self.main_db)
        self.face = FaceAttendanceStore(self.face_db)
        self.groups = GroupVisitStore(self.face_db)
        self.integration = IntegrationHub(self.main_db)
        self.state = StateStore(self.main_db)
        self.reports = ReportService(self.main_db, self.face_db)

    def tearDown(self):
        self.temp.cleanup()

    def _create_visit(self, name="Test Visitor", email="test.visitor@example.test"):
        visit_date = (datetime.now(SCHOOL_TIMEZONE).date() + timedelta(days=1)).isoformat()
        result = self.domain.create_visit({
            "id": "VMS-REPORT-001", "name": name, "email": email, "host": "Test Host",
            "destination": "Main Reception", "purpose": "Report export test", "visitDate": visit_date,
            "window": "09:00-10:00", "visitorTypeId": "general-visitor",
        })
        return result

    def test_catalog_lists_all_applicable_report_types_and_formats(self):
        catalog = self.reports.catalog()
        self.assertTrue(catalog["ok"])
        self.assertEqual({r["id"] for r in catalog["reports"]}, {r["id"] for r in REPORT_DEFINITIONS})
        self.assertEqual(catalog["formats"], ["csv", "xlsx", "json", "pdf", "html"])
        self.assertGreaterEqual(len(catalog["reports"]), 15)
        by_id = {r["id"]: r for r in catalog["reports"]}
        self.assertFalse(by_id["replacement_card_queue"]["supportsDateFilter"])
        self.assertFalse(by_id["ai_event_review"]["supportsDateFilter"])
        self.assertTrue(by_id["visitor_register"]["supportsDateFilter"])

    def test_visitor_register_privacy_is_default_and_contact_is_explicit(self):
        self._create_visit()
        ordinary = self.reports.preview({"reportId": "visitor_register"})
        self.assertEqual(ordinary["rowCount"], 1)
        self.assertNotIn("visitorEmail", [x["key"] for x in ordinary["columns"]])
        self.assertNotIn("visitorEmail", ordinary["rows"][0])
        explicit = self.reports.preview({"reportId": "visitor_register", "includeContactDetails": True})
        self.assertIn("visitorEmail", [x["key"] for x in explicit["columns"]])
        self.assertEqual(explicit["rows"][0]["visitorEmail"], "test.visitor@example.test")

    def test_date_and_search_filters_apply_to_source_records(self):
        self._create_visit(name="Alpha Visitor")
        rows = self.reports.preview({"reportId": "visitor_register", "filters": {"search": "Alpha Visitor"}})
        self.assertEqual(rows["rowCount"], 1)
        none = self.reports.preview({"reportId": "visitor_register", "filters": {"search": "not-present"}})
        self.assertEqual(none["rowCount"], 0)
        summary = self.reports.preview({"reportId": "visitor_type_summary", "filters": {"visitorType": "General Visitor"}})
        self.assertEqual(summary["rowCount"], 1)

    def test_csv_formula_injection_is_escaped(self):
        self._create_visit(name="=1+1")
        filename, content_type, body, _ = self.reports.export({"reportId": "visitor_register", "format": "csv"})
        self.assertEqual(content_type, "text/csv; charset=utf-8")
        self.assertIn(b"'=1+1", body)
        self.assertTrue(filename.endswith(".csv"))

    def test_all_formats_are_real_downloadable_files(self):
        self._create_visit()
        cases = {
            "csv": ("text/csv; charset=utf-8", b"Booking ID"),
            "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK"),
            "json": ("application/json; charset=utf-8", b'"reportId": "visitor_register"'),
            "pdf": ("application/pdf", b"%PDF-1.4"),
            "html": ("text/html; charset=utf-8", b"<!doctype html>"),
        }
        for fmt, (expected_type, marker) in cases.items():
            with self.subTest(fmt=fmt):
                filename, content_type, body, metadata = self.reports.export({"reportId": "visitor_register", "format": fmt})
                self.assertEqual(content_type, expected_type)
                self.assertIn(marker, body)
                self.assertIn("stamford-vms-visitor-register-", filename)
                self.assertEqual(metadata["rowCount"], 1)
                if fmt == "xlsx":
                    with zipfile.ZipFile(io.BytesIO(body)) as archive:
                        self.assertIsNone(archive.testzip())
                        sheet = archive.read("xl/worksheets/sheet1.xml")
                        self.assertIn(b"Booking ID", sheet)
                        from xml.etree import ElementTree
                        for name in ("[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml", "xl/styles.xml"):
                            ElementTree.fromstring(archive.read(name))
                if fmt == "json":
                    self.assertEqual(json.loads(body.decode("utf-8"))["rows"][0]["visitorName"], "Test Visitor")
                if fmt == "pdf":
                    self.assertTrue(body.endswith(b"%%EOF\n"))

    def test_all_report_catalog_entries_can_generate_empty_or_populated_preview(self):
        self._create_visit()
        for definition in REPORT_DEFINITIONS:
            with self.subTest(report=definition["id"]):
                data = self.reports.preview({"reportId": definition["id"]})
                self.assertTrue(data["ok"])
                self.assertEqual(data["reportId"], definition["id"])
                self.assertIsInstance(data["columns"], list)
                self.assertIsInstance(data["rows"], list)
                self.assertIsInstance(data["note"], str)

    def test_snapshot_reports_reject_unreliable_date_filters(self):
        for report_id in ("replacement_card_queue", "ai_event_review"):
            with self.subTest(report=report_id), self.assertRaises(ReportingError) as raised:
                self.reports.preview({"reportId": report_id, "filters": {"fromDate": "2026-01-01"}})
            self.assertEqual(raised.exception.code, "DATE_FILTER_UNAVAILABLE")

    def test_bad_report_id_date_range_and_format_fail_closed(self):
        with self.assertRaises(ReportingError):
            self.reports.preview({"reportId": "unknown-report"})
        with self.assertRaises(ReportingError):
            self.reports.preview({"reportId": "visitor_register", "filters": {"fromDate": "2026-02-30"}})
        with self.assertRaises(ReportingError):
            self.reports.preview({"reportId": "visitor_register", "filters": {"fromDate": "2026-12-01", "toDate": "2026-01-01"}})
        with self.assertRaises(ReportingError):
            self.reports.export({"reportId": "visitor_register", "format": "exe"})


class ReportingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.main_db = root / "prototype-state.sqlite3"
        self.face_db = root / "face-attendance.sqlite3"
        self.ui_dir = ROOT / "ui"
        self.store = StateStore(self.main_db)
        self.domain = DomainStore(self.main_db)
        self.face = FaceAttendanceStore(self.face_db)
        self.integration = IntegrationHub(self.main_db)
        self.groups = GroupVisitStore(self.face_db)
        handler = make_handler(self.ui_dir, self.store, self.domain, self.face, self.integration, self.groups)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_catalog_preview_and_download_routes(self):
        code, _, body = self.request("GET", "/api/v1/reports/catalog")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["ok"], True)
        code, _, body = self.request("POST", "/api/v1/reports/preview", {"reportId": "visitor_register"})
        self.assertEqual(code, 200)
        self.assertIn("columns", json.loads(body))
        code, headers, body = self.request("POST", "/api/v1/reports/export", {"reportId": "visitor_register", "format": "xlsx"})
        self.assertEqual(code, 200)
        self.assertIn(".xlsx", headers.get("Content-Disposition", ""))
        self.assertTrue(body.startswith(b"PK"))

    def test_export_errors_are_json_not_unhandled_500(self):
        code, headers, body = self.request("POST", "/api/v1/reports/export", {"reportId": "not_a_report", "format": "csv"})
        self.assertEqual(code, 404)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertEqual(json.loads(body)["error"], "REPORT_NOT_FOUND")
        code, headers, body = self.request("POST", "/api/v1/reports/preview", {"reportId": "ai_event_review", "filters": {"fromDate": "2026-01-01"}})
        self.assertEqual(code, 400)
        self.assertEqual(json.loads(body)["error"], "DATE_FILTER_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()

"""Tests for bounded Genetec roster import and staff-confirmed local attendance."""
import json
from contextlib import closing
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from service import face_attendance as face
from service.face_attendance import FaceAttendanceError, FaceAttendanceStore


CARDHOLDER_GUID = "11111111-1111-4111-8111-111111111111"
OTHER_GUID = "22222222-2222-4222-8222-222222222222"
CREDENTIAL_GUID = "33333333-3333-4333-8333-333333333333"


def bridge_response():
    return {
        "success": True,
        "mode": "read-only-genetec-student-roster-prefix-import",
        "externalIdPrefix": "STU-",
        "scannedCount": 3,
        "matchedCount": 2,
        "returnedCount": 2,
        "resultsLimited": False,
        "rawCredentialNumbersReturned": False,
        "imageBytesReturned": False,
        "writeAttempted": False,
        "cardholders": [
            {
                "cardholderGuid": CARDHOLDER_GUID,
                "displayName": "Test Student One",
                "firstName": "Test",
                "lastName": "Student One",
                "externalId": "STU-1001",
                "cardholderGroups": ["MS & HS"],
                "photoAvailable": True,
                "credentialReferences": [{"guid": CREDENTIAL_GUID, "name": "School card", "maskedIdentifier": "••••1234", "active": True}],
            },
            {
                "cardholderGuid": OTHER_GUID,
                "displayName": "Test Student Two",
                "firstName": "Test",
                "lastName": "Student Two",
                "externalId": "STU-1002",
                "cardholderGroups": ["LE & UE"],
                "photoAvailable": False,
                "credentialReferences": [],
            },
        ],
    }


class GenetecRosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FaceAttendanceStore(
            Path(self.temp.name) / "face-attendance.sqlite3",
            protector=lambda data, description: b"encrypted:" + data,
            unprotector=lambda data: data.removeprefix(b"encrypted:"),
        )
        self.gate = patch("service.face_attendance._require_genetec_cardholder_face_sync", lambda: None)
        self.gate.start()

    def tearDown(self):
        self.gate.stop()
        self.temp.cleanup()

    def test_bulk_import_is_prefix_scoped_and_masks_credentials(self):
        with patch("service.face_attendance._genetec_bridge_get", return_value=bridge_response()) as bridge:
            result = self.store.import_genetec_student_roster({"externalIdPrefix": "STU-", "limit": 1000})
        bridge.assert_called_once_with("/api/genetec/student-roster", {"externalIdPrefix": "STU-", "limit": "1000"})
        self.assertEqual(result["importedCount"], 2)
        self.assertEqual(result["insertedCount"], 2)
        self.assertFalse(result["portraitsPersisted"])
        roster = self.store.list_genetec_student_roster("")
        self.assertEqual(roster["count"], 2)
        self.assertEqual(roster["cardholders"][0]["externalId"], "STU-1001")
        self.assertEqual(roster["cardholders"][0]["credentialReferences"][0]["maskedIdentifier"], "••••1234")
        with closing(self.store._connect()) as conn:
            schema = {row[1] for row in conn.execute("PRAGMA table_info(genetec_cardholder_roster)")}
            self.assertNotIn("portrait_bytes", schema)
            self.assertNotIn("image_base64", schema)

    def test_group_catalog_returns_only_group_names_and_counts(self):
        payload = {"success": True, "groups": [{"name": "MS & HS", "cardholderCount": 240}, {"name": "Parent", "cardholderCount": 95}], "groupCount": 2, "portraitsReturned": False, "rawCredentialNumbersReturned": False}
        with patch("service.face_attendance._genetec_bridge_get", return_value=payload) as bridge:
            result = self.store.list_genetec_student_groups()
        bridge.assert_called_once_with("/api/genetec/student-roster-groups")
        self.assertEqual([group["name"] for group in result["groups"]], ["MS & HS", "Parent"])
        self.assertFalse(result["rawCredentialNumbersReturned"])

    def test_bulk_import_rejects_unscoped_and_invalid_limits(self):
        with self.assertRaises(FaceAttendanceError) as ctx:
            self.store.import_genetec_student_roster({"limit": 1000})
        self.assertEqual(ctx.exception.code, "STUDENT_SCOPE_REQUIRED")
        with self.assertRaises(FaceAttendanceError) as ctx:
            self.store.import_genetec_student_roster({"externalIdPrefix": "*", "limit": 1000})
        self.assertEqual(ctx.exception.code, "STUDENT_ID_PREFIX_INVALID")
        with self.assertRaises(FaceAttendanceError) as ctx:
            self.store.import_genetec_student_roster({"externalIdPrefix": "STU-", "limit": 5000})
        self.assertEqual(ctx.exception.code, "STUDENT_ROSTER_LIMIT_INVALID")

    def test_bulk_import_can_be_scoped_by_exact_cardholder_groups_without_external_id_prefix(self):
        response = bridge_response()
        response["cardholders"][0]["externalId"] = ""
        response["cardholders"][0]["cardholderGroups"] = ["MS & HS"]
        response["cardholders"][1]["externalId"] = ""
        response["cardholders"][1]["cardholderGroups"] = ["LE & UE"]
        with patch("service.face_attendance._genetec_bridge_get", return_value=response) as bridge:
            result = self.store.import_genetec_student_roster({"groupNames": ["MS & HS"], "limit": 1000})
        bridge.assert_called_once_with("/api/genetec/student-roster", {"limit": "1000", "groupNames": "MS & HS"})
        self.assertEqual(result["importedCount"], 1)
        roster = self.store.list_genetec_student_roster("")
        self.assertEqual(roster["count"], 1)
        self.assertEqual(roster["cardholders"][0]["cardholderGroups"], ["MS & HS"])
        self.assertTrue(roster["cardholders"][0]["externalId"].startswith("GC-"))

    def test_bulk_import_group_prefix_combination_is_restrictive(self):
        response = bridge_response()
        response["cardholders"][1]["externalId"] = "STU-1002"
        response["cardholders"][1]["cardholderGroups"] = ["LE & UE"]
        with patch("service.face_attendance._genetec_bridge_get", return_value=response) as bridge:
            self.store.import_genetec_student_roster({"groupNames": ["LE & UE"], "externalIdPrefix": "STU-", "limit": 1000})
        bridge.assert_called_once_with("/api/genetec/student-roster", {"limit": "1000", "externalIdPrefix": "STU-", "groupNames": "LE & UE"})
        self.assertEqual(self.store.list_genetec_student_roster("")["count"], 1)

    def test_reimport_updates_existing_guid_instead_of_duplicating(self):
        with patch("service.face_attendance._genetec_bridge_get", return_value=bridge_response()):
            first = self.store.import_genetec_student_roster({"externalIdPrefix": "STU-", "limit": 1000})
            second = self.store.import_genetec_student_roster({"externalIdPrefix": "STU-", "limit": 1000})
        self.assertEqual(first["insertedCount"], 2)
        self.assertEqual(second["insertedCount"], 0)
        self.assertEqual(second["updatedCount"], 2)
        self.assertEqual(self.store.list_genetec_student_roster()["count"], 2)

    def _import(self):
        with patch("service.face_attendance._genetec_bridge_get", return_value=bridge_response()):
            self.store.import_genetec_student_roster({"externalIdPrefix": "STU-", "limit": 1000})

    def test_portrait_is_fetched_only_for_imported_cardholder_and_not_saved(self):
        self._import()
        payload = {
            "success": True,
            "cardholder": {"cardholderGuid": CARDHOLDER_GUID, "displayName": "Test Student One", "externalId": "STU-1001"},
            "imageMimeType": "image/png",
            "imageBase64": "iVBORw0KGgoAAAANSUhEUg==",
            "persistedByBridge": False,
        }
        with patch("service.face_attendance._genetec_bridge_get", return_value=payload) as bridge:
            result = self.store.get_genetec_roster_portrait(CARDHOLDER_GUID)
        bridge.assert_called_once_with("/api/genetec/cardholder-photo", {"cardholderGuid": CARDHOLDER_GUID})
        self.assertTrue(result["imageDataUrl"].startswith("data:image/png;base64,"))
        self.assertFalse(result["persisted"])
        with self.assertRaises(FaceAttendanceError) as ctx:
            self.store.get_genetec_roster_portrait(str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "CARDHOLDER_NOT_IN_ROSTER")

    def test_attendance_requires_loaded_portrait_and_explicit_staff_confirmation(self):
        self._import()
        base = {"sessionId": "ATT-2026-10-18", "operator": "Test Operator", "eventType": "CheckIn"}
        with self.assertRaises(FaceAttendanceError) as ctx:
            self.store.record_genetec_roster_attendance(CARDHOLDER_GUID, {**base, "portraitViewed": False, "confirmIdentity": True})
        self.assertEqual(ctx.exception.code, "STAFF_PORTRAIT_CONFIRMATION_REQUIRED")
        with self.assertRaises(FaceAttendanceError) as ctx:
            self.store.record_genetec_roster_attendance(CARDHOLDER_GUID, {**base, "portraitViewed": True, "confirmIdentity": False})
        self.assertEqual(ctx.exception.code, "STAFF_PORTRAIT_CONFIRMATION_REQUIRED")
        result = self.store.record_genetec_roster_attendance(CARDHOLDER_GUID, {**base, "portraitViewed": True, "confirmIdentity": True})
        self.assertEqual(result["eventType"], "CheckIn")
        self.assertFalse(result["writtenToGenetec"])

    def test_checkout_requires_checkin_and_duplicate_events_are_rejected(self):
        self._import()
        base = {"sessionId": "ATT-2026-10-18", "operator": "Test Operator", "portraitViewed": True, "confirmIdentity": True}
        with self.assertRaises(FaceAttendanceError) as ctx:
            self.store.record_genetec_roster_attendance(CARDHOLDER_GUID, {**base, "eventType": "CheckOut"})
        self.assertEqual(ctx.exception.code, "CHECKIN_REQUIRED")
        self.store.record_genetec_roster_attendance(CARDHOLDER_GUID, {**base, "eventType": "CheckIn"})
        checkout = self.store.record_genetec_roster_attendance(CARDHOLDER_GUID, {**base, "eventType": "CheckOut"})
        self.assertEqual(checkout["eventType"], "CheckOut")
        with self.assertRaises(FaceAttendanceError) as ctx:
            self.store.record_genetec_roster_attendance(CARDHOLDER_GUID, {**base, "eventType": "CheckOut"})
        self.assertEqual(ctx.exception.code, "ATTENDANCE_ALREADY_RECORDED")
        events = self.store.list_genetec_roster_attendance("ATT-2026-10-18")["events"]
        self.assertEqual([e["eventType"] for e in events], ["CheckOut", "CheckIn"])


if __name__ == "__main__":
    unittest.main()

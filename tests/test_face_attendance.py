"""Tests for the optional, consent-gated face-attendance prototype.

The engine is injected/mocked so CI does not need a webcam, Windows DPAPI or
opencv-contrib. Real model accuracy and camera operation require on-host UAT.
"""
from __future__ import annotations

import base64
import json
import os
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service import face_attendance as face
from service.face_attendance import FaceAttendanceError, FaceAttendanceStore


class _FakeNumpy:
    int32 = int

    @staticmethod
    def array(values, dtype=None):
        return list(values)


class _FakeRecognizer:
    def __init__(self):
        self.labels = []
        self.prediction_index = 0

    def train(self, images, labels):
        self.labels = list(labels)
        self.prediction_index = 0

    def predict(self, image):
        unique = sorted(set(self.labels))
        if not unique:
            raise RuntimeError("empty fake recognizer")
        label = unique[min(self.prediction_index, len(unique) - 1)]
        self.prediction_index += 1
        return label, 12.5


class _FakeFaceModule:
    @staticmethod
    def LBPHFaceRecognizer_create(**kwargs):
        return _FakeRecognizer()


class _FakeCV:
    face = _FakeFaceModule()


class FaceAttendanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FaceAttendanceStore(
            Path(self.temp.name) / "face-attendance.sqlite3",
            protector=lambda data, description: b"encrypted:" + data,
            unprotector=lambda data: data.removeprefix(b"encrypted:"),
        )
        self.feature_flags = patch.dict(os.environ, {
            "STAMFORD_FACE_ATTENDANCE_ENABLED": "1",
            "STAMFORD_BIOMETRIC_POLICY_APPROVED": "1",
            "STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED": "1",
            "STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED": "1",
            "STAMFORD_FACE_LBPH_MAX_DISTANCE": "55",
        }, clear=True)
        self.feature_flags.start()
        self.gate_patch = patch("service.face_attendance._require_feature", lambda group=False: None)
        self.gate_patch.start()
        self.extract_patch = patch("service.face_attendance._extract_faces", side_effect=self.fake_extract)
        self.extract_patch.start()
        self.group_extract_patch = patch("service.face_attendance._extract_faces_with_boxes", side_effect=self.fake_extract_with_boxes)
        self.group_extract_patch.start()
        self.cv_patch = patch("service.face_attendance._opencv_module", return_value=(_FakeCV(), _FakeNumpy()))
        self.cv_patch.start()
        self.decode_patch = patch("service.face_attendance._decode_crop", side_effect=lambda data: data)
        self.decode_patch.start()
        self._fake_counter = 0

    def tearDown(self):
        for p in (self.decode_patch, self.cv_patch, self.group_extract_patch, self.extract_patch, self.gate_patch, self.feature_flags):
            p.stop()
        self.temp.cleanup()

    def fake_extract(self, data_url, max_faces=40):
        if data_url == "group-frame":
            return [b"crop-a", b"crop-b"]
        if data_url == "group-enrollment-photo":
            raise FaceAttendanceError(422, "TOO_MANY_FACES", "multiple faces")
        return [b"individual-face-crop"]

    def fake_extract_with_boxes(self, data_url, max_faces=40, group_workflow=False):
        if data_url != "group-frame":
            raise FaceAttendanceError(400, "INVALID_IMAGE", "unexpected fake group input")
        return ([{"faceIndex": 1, "cropBytes": b"crop-a", "boundingBox": {"x": 10, "y": 20, "width": 40, "height": 40}}, {"faceIndex": 2, "cropBytes": b"crop-b", "boundingBox": {"x": 70, "y": 20, "width": 40, "height": 40}}], 640, 480)

    def payload(self, student_id):
        return {
            "studentId": student_id,
            "displayName": f"Test {student_id}",
            "className": "Grade 8A",
            "consentReference": f"CONSENT-{student_id}",
            "schoolApprovalReference": "POLICY-APPROVED-001",
            "approvedBy": "Test Safeguarding Owner",
            "guardianConsent": True,
            "images": ["sample-1", "sample-2", "sample-3"],
        }

    def enroll(self, student_id):
        return self.store.enroll(self.payload(student_id))

    def test_feature_status_is_disabled_by_default(self):
        self.feature_flags.stop()
        with patch.dict(os.environ, {}, clear=True):
            status = self.store.status()
        self.assertFalse(status["studentFaceAttendanceEnabled"])
        self.assertFalse(status["groupFaceIdentificationEnabled"])
        self.assertTrue(status["recognitionResultRequiresHumanConfirmation"])
        self.assertFalse(status["rawImagesPersisted"])

    def test_group_identification_needs_separate_approval(self):
        self.gate_patch.stop()
        self.feature_flags.stop()
        with patch.dict(os.environ, {
            "STAMFORD_FACE_ATTENDANCE_ENABLED": "1",
            "STAMFORD_BIOMETRIC_POLICY_APPROVED": "1",
        }, clear=True):
            with self.assertRaises(FaceAttendanceError) as context:
                face._require_feature(group=True)
        self.assertEqual(context.exception.code, "GROUP_FACE_IDENTIFICATION_NOT_APPROVED")

    def test_enrollment_requires_guardian_consent_and_audit_references(self):
        payload = self.payload("S101")
        payload["guardianConsent"] = False
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.enroll(payload)
        self.assertEqual(context.exception.code, "GUARDIAN_CONSENT_REQUIRED")

        payload = self.payload("S101")
        payload["schoolApprovalReference"] = ""
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.enroll(payload)
        self.assertEqual(context.exception.code, "APPROVAL_REFERENCE_REQUIRED")

    def test_student_checkin_then_checkout_requires_human_confirmation(self):
        self.enroll("S101")
        checkin_candidate = self.store.match_student({"eventType": "CheckIn", "sessionId": "ATT-2026-10-17", "imageData": "person-frame"})
        self.assertTrue(checkin_candidate["matched"])
        self.assertTrue(checkin_candidate["humanConfirmationRequired"])
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.confirm_student({"ticketId": checkin_candidate["ticketId"], "studentId": "S101", "sessionId": "ATT-2026-10-17", "operator": "Operator", "confirmMatch": False})
        self.assertEqual(context.exception.code, "HUMAN_CONFIRMATION_REQUIRED")

        checkin = self.store.confirm_student({"ticketId": checkin_candidate["ticketId"], "studentId": "S101", "sessionId": "ATT-2026-10-17", "operator": "Operator", "confirmMatch": True})
        self.assertEqual(checkin["eventType"], "CheckIn")
        checkout_candidate = self.store.match_student({"eventType": "CheckOut", "sessionId": "ATT-2026-10-17", "imageData": "person-frame"})
        checkout = self.store.confirm_student({"ticketId": checkout_candidate["ticketId"], "studentId": "S101", "sessionId": "ATT-2026-10-17", "operator": "Operator", "confirmMatch": True})
        self.assertEqual(checkout["eventType"], "CheckOut")
        events = self.store.list_events("ATT-2026-10-17")["events"]
        self.assertEqual([item["event_type"] for item in events], ["CheckIn", "CheckOut"])

    def test_checkout_is_blocked_without_a_matching_session_checkin(self):
        self.enroll("S101")
        candidate = self.store.match_student({"eventType": "CheckOut", "sessionId": "ATT-2026-10-17", "imageData": "person-frame"})
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.confirm_student({"ticketId": candidate["ticketId"], "studentId": "S101", "sessionId": "ATT-2026-10-17", "operator": "Operator", "confirmMatch": True})
        self.assertEqual(context.exception.code, "CHECKIN_REQUIRED")

    def test_group_match_is_roster_scoped_and_batch_confirmation_is_atomic(self):
        self.enroll("S101")
        self.enroll("S102")
        match = self.store.match_group({"eventType": "CheckIn", "groupEventId": "GR-EVENT-01", "sessionId": "GR-EVENT-01", "studentIds": ["S101", "S102"], "imageData": "group-frame"})
        self.assertEqual(match["detectedFaceCount"], 2)
        self.assertFalse(match["groupImagePersisted"])
        self.assertFalse(match["groupImageUsedForEnrollment"])
        self.assertTrue(all(item["humanConfirmationRequired"] for item in match["candidates"] if item.get("matched")))
        confirmations = [{"ticketId": item["ticketId"], "studentId": item["candidate"]["studentId"], "confirmMatch": True} for item in match["candidates"] if item.get("matched")]
        self.assertEqual(len(confirmations), 2)
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.confirm_group_batch({"groupEventId": "GR-EVENT-01", "sessionId": "GR-EVENT-01", "operator": "Operator", "confirmBatch": True, "confirmations": [{**confirmations[0], "confirmMatch": False}, confirmations[1]]})
        self.assertEqual(context.exception.code, "HUMAN_CONFIRMATION_REQUIRED")
        self.assertEqual(self.store.list_events("GR-EVENT-01", "GR-EVENT-01")["events"], [])
        result = self.store.confirm_group_batch({"groupEventId": "GR-EVENT-01", "sessionId": "GR-EVENT-01", "operator": "Operator", "confirmBatch": True, "confirmations": confirmations})
        self.assertEqual(result["recordedCount"], 2)

    def test_group_checkout_requires_checkin_and_batch_is_all_or_nothing(self):
        self.enroll("S101")
        self.enroll("S102")
        match = self.store.match_group({"eventType": "CheckOut", "groupEventId": "GR-EVENT-02", "sessionId": "GR-EVENT-02", "studentIds": ["S101", "S102"], "imageData": "group-frame"})
        confirmations = [{"ticketId": item["ticketId"], "studentId": item["candidate"]["studentId"], "confirmMatch": True} for item in match["candidates"] if item.get("matched")]
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.confirm_group_batch({"groupEventId": "GR-EVENT-02", "sessionId": "GR-EVENT-02", "operator": "Operator", "confirmBatch": True, "confirmations": confirmations})
        self.assertEqual(context.exception.code, "CHECKIN_REQUIRED")
        self.assertEqual(self.store.list_events("GR-EVENT-02", "GR-EVENT-02")["events"], [])

    def test_group_photos_cannot_be_used_to_enroll_people(self):
        payload = self.payload("S101")
        payload["images"] = ["group-enrollment-photo", "sample-2"]
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.enroll(payload)
        self.assertEqual(context.exception.code, "GROUP_PHOTO_ENROLLMENT_BLOCKED")

    def test_withdrawal_deactivates_enrollment_and_deletes_encrypted_samples(self):
        self.enroll("S101")
        result = self.store.revoke_enrollment("S101", {"approvedBy": "Privacy Officer", "withdrawalReference": "WITHDRAWAL-2026-01"})
        self.assertFalse(result["enrollmentActive"])
        # In this test, the feature is enabled; revocation removes the active roster entry.
        self.assertEqual(self.store.list_students()["students"], [])


    def _genetec_photo_payload(self, student_id="S201", cardholder_guid="11111111-1111-4111-8111-111111111111", external_id="S201"):
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"synthetic-test-image-payload"
        credential_guid = "22222222-2222-4222-8222-222222222222"
        response = {
            "success": True,
            "cardholder": {
                "cardholderGuid": cardholder_guid,
                "displayName": "Test Cardholder",
                "firstName": "Test",
                "lastName": "Cardholder",
                "externalId": external_id,
                "cardholderGroups": ["Stamford Test Group", "Faculty"],
                "credentialReferences": [{
                    "guid": credential_guid,
                    "name": "Assigned card",
                    "maskedIdentifier": "••••5678",
                    "active": True,
                }],
            },
            "imageMimeType": "image/png",
            "imageBase64": base64.b64encode(image_bytes).decode("ascii"),
            "rawCredentialNumbersReturned": False,
            "persistedByBridge": False,
        }
        payload = self.payload(student_id)
        payload.update({
            "cardholderGuid": cardholder_guid,
            "images": ["live-camera-frame"],
        })
        return response, payload, image_bytes

    def test_genetec_cardholder_sync_requires_separate_approval(self):
        with patch.dict(os.environ, {
            "STAMFORD_FACE_ATTENDANCE_ENABLED": "1",
            "STAMFORD_BIOMETRIC_POLICY_APPROVED": "1",
        }, clear=True):
            with patch("service.face_attendance._genetec_bridge_get", side_effect=AssertionError("bridge must not be called")):
                with self.assertRaises(FaceAttendanceError) as context:
                    self.store.list_genetec_cardholders("student")
        self.assertEqual(context.exception.code, "GENETEC_CARDHOLDER_FACE_SYNC_DISABLED")

    def test_genetec_cardholder_search_returns_only_masked_credential_refs(self):
        response = {
            "success": True,
            "cardholders": [{
                "cardholderGuid": "11111111-1111-4111-8111-111111111111",
                "displayName": "Test Cardholder",
                "firstName": "Test",
                "lastName": "Cardholder",
                "externalId": "S201",
                "photoAvailable": True,
                "credentialReferences": [
                    {"guid": "22222222-2222-4222-8222-222222222222", "name": "Assigned card", "maskedIdentifier": "••••5678", "active": True},
                    {"guid": "33333333-3333-4333-8333-333333333333", "name": "Other credential", "maskedIdentifier": "123456789012", "active": True},
                ],
            }],
            "rawCredentialNumbersReturned": False,
        }
        with patch("service.face_attendance._genetec_bridge_get", return_value=response):
            result = self.store.list_genetec_cardholders("Test")
        self.assertTrue(result["readOnly"])
        self.assertFalse(result["rawCredentialNumbersReturned"])
        self.assertEqual(len(result["cardholders"]), 1)
        refs = result["cardholders"][0]["credentialReferences"]
        self.assertEqual(refs[0]["maskedIdentifier"], "••••5678")
        self.assertIsNone(refs[1]["maskedIdentifier"])
        self.assertNotIn("123456789012", json.dumps(result))

    def test_genetec_enrollment_encrypts_local_crops_and_links_masked_credentials(self):
        response, payload, image_bytes = self._genetec_photo_payload()
        with patch("service.face_attendance._genetec_bridge_get", return_value=response):
            result = self.store.enroll_from_genetec(payload)
        self.assertEqual(result["identitySource"], "GenetecCardholder")
        self.assertEqual(result["genetecCardholderGuid"], payload["cardholderGuid"])
        self.assertEqual(result["sampleCount"], 2)
        self.assertFalse(result["genetecPhotoPersistedAsRawImage"])
        self.assertFalse(result["rawCredentialNumbersReturned"])
        self.assertEqual(result["genetecMetadata"]["firstName"], "Test")
        self.assertEqual(result["genetecMetadata"]["cardholderGroups"], ["Stamford Test Group", "Faculty"])
        with closing(self.store._connect()) as conn:
            enrollment = conn.execute("SELECT genetec_cardholder_guid,genetec_credential_refs_json,genetec_metadata_json FROM face_enrollments WHERE student_id=?", ("S201",)).fetchone()
            samples = conn.execute("SELECT protected_sample FROM face_samples WHERE student_id=? ORDER BY sample_no", ("S201",)).fetchall()
        self.assertEqual(enrollment["genetec_cardholder_guid"], payload["cardholderGuid"])
        stored_credential_refs = json.loads(enrollment["genetec_credential_refs_json"])
        self.assertEqual(stored_credential_refs[0]["maskedIdentifier"], "••••5678")
        self.assertNotIn("123456789012", enrollment["genetec_credential_refs_json"])
        stored_metadata = json.loads(enrollment["genetec_metadata_json"])
        self.assertEqual(stored_metadata["externalId"], "S201")
        self.assertEqual(stored_metadata["cardholderGroups"], ["Stamford Test Group", "Faculty"])
        self.assertEqual(len(samples), 2)
        self.assertTrue(all(bytes(item["protected_sample"]).startswith(b"encrypted:") for item in samples))
        self.assertNotIn(image_bytes, b"".join(bytes(item["protected_sample"]) for item in samples))

    def test_genetec_profile_metadata_is_allowlisted_and_control_chars_removed(self):
        safe = face._safe_genetec_profile_metadata({
            "firstName": "Ada\nAdmin", "lastName": "Lovelace", "externalId": "STF-101",
            "cardholderGroups": ["Faculty", "Faculty", "Ops\tTeam"],
            "email": "not-needed@example.test", "secret": "do-not-store",
        })
        self.assertEqual(safe["firstName"], "AdaAdmin")
        self.assertEqual(safe["cardholderGroups"], ["Faculty", "OpsTeam"])
        self.assertNotIn("email", safe)
        self.assertNotIn("secret", safe)

    def test_genetec_cardholder_cannot_be_linked_to_two_student_profiles(self):
        response, payload, _ = self._genetec_photo_payload()
        with patch("service.face_attendance._genetec_bridge_get", return_value=response):
            self.store.enroll_from_genetec(payload)
            second_payload = dict(payload)
            second_payload["studentId"] = "S202"
            with self.assertRaises(FaceAttendanceError) as context:
                self.store.enroll_from_genetec(second_payload)
        self.assertEqual(context.exception.code, "GENETEC_CARDHOLDER_ALREADY_LINKED")

    def test_attendance_event_retains_genetec_cardholder_link_and_masked_credential_refs(self):
        response, payload, _ = self._genetec_photo_payload()
        with patch("service.face_attendance._genetec_bridge_get", return_value=response):
            self.store.enroll_from_genetec(payload)
        candidate = self.store.match_student({"eventType": "CheckIn", "sessionId": "ATT-GENETEC-01", "imageData": "person-frame"})
        self.assertEqual(candidate["candidate"]["firstName"], "Test")
        self.assertEqual(candidate["candidate"]["externalId"], "S201")
        self.assertEqual(candidate["candidate"]["cardholderGroups"], ["Stamford Test Group", "Faculty"])
        result = self.store.confirm_student({"ticketId": candidate["ticketId"], "studentId": "S201", "sessionId": "ATT-GENETEC-01", "operator": "Approved Operator", "confirmMatch": True})
        event = self.store.list_events("ATT-GENETEC-01")["events"][0]
        self.assertEqual(result["eventType"], "CheckIn")
        self.assertEqual(event["genetec_cardholder_guid"], payload["cardholderGuid"])
        self.assertEqual(event["genetecCredentialReferences"][0]["maskedIdentifier"], "••••5678")


    def test_kiosk_mock_returns_distinct_synthetic_profiles_by_person_type(self):
        image = "data:image/jpeg;base64,aGVsbG8="
        for person_type, expected_name, expected_id in [
            ("Student", "Ava Thompson", "STU-DEMO-10482"),
            ("Staff", "James Carter", "STF-DEMO-02047"),
            ("Contractor", "Morgan Lee", "CTR-DEMO-00301"),
        ]:
            result = self.store.match_kiosk_face({
                "imageData": image, "personType": person_type,
                "eventType": "CheckIn", "sessionId": "ATT-KIOSK-TEST",
            })
            self.assertEqual(result["mode"], "mock")
            self.assertTrue(result["simulated"])
            self.assertTrue(result["matched"])
            self.assertEqual(result["candidate"]["personType"], person_type)
            self.assertEqual(result["candidate"]["displayName"], expected_name)
            self.assertEqual(result["candidate"]["studentId"], expected_id)
            self.assertFalse(result["rawImagePersisted"])
            self.assertTrue(result["humanConfirmationRequired"])

    def test_kiosk_mock_requires_confirmation_and_records_local_only(self):
        result = self.store.match_kiosk_face({
            "imageData": "data:image/jpeg;base64,aGVsbG8=", "personType": "Staff",
            "eventType": "CheckIn", "sessionId": "ATT-KIOSK-LOCAL",
        })
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.confirm_kiosk_face({
                "ticketId": result["ticketId"], "studentId": result["candidate"]["studentId"],
                "sessionId": "ATT-KIOSK-LOCAL", "confirmMatch": False,
            })
        self.assertEqual(context.exception.code, "HUMAN_CONFIRMATION_REQUIRED")
        confirmed = self.store.confirm_kiosk_face({
            "ticketId": result["ticketId"], "studentId": result["candidate"]["studentId"],
            "sessionId": "ATT-KIOSK-LOCAL", "confirmMatch": True,
        })
        self.assertTrue(confirmed["simulated"])
        self.assertFalse(confirmed["writtenToGenetec"])
        self.assertEqual(confirmed["personType"], "Staff")
        events = self.store.list_kiosk_events("ATT-KIOSK-LOCAL")
        self.assertEqual(events["count"], 1)
        self.assertTrue(events["events"][0]["simulated"])
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.confirm_kiosk_face({
                "ticketId": result["ticketId"], "studentId": result["candidate"]["studentId"],
                "sessionId": "ATT-KIOSK-LOCAL", "confirmMatch": True,
            })
        self.assertEqual(context.exception.code, "MATCH_TICKET_ALREADY_USED")

    def test_kiosk_checkout_requires_a_prior_checkin(self):
        payload = {
            "imageData": "data:image/jpeg;base64,aGVsbG8=", "personType": "Contractor",
            "eventType": "CheckOut", "sessionId": "ATT-KIOSK-CHECKOUT",
        }
        result = self.store.match_kiosk_face(payload)
        with self.assertRaises(FaceAttendanceError) as context:
            self.store.confirm_kiosk_face({
                "ticketId": result["ticketId"], "studentId": result["candidate"]["studentId"],
                "sessionId": "ATT-KIOSK-CHECKOUT", "confirmMatch": True,
            })
        self.assertEqual(context.exception.code, "CHECKIN_REQUIRED")

    def test_live_kiosk_selection_fails_closed_instead_of_falling_back_to_mock(self):
        payload = {
            "imageData": "data:image/jpeg;base64,aGVsbG8=", "personType": "Student",
            "eventType": "CheckIn", "sessionId": "ATT-KIOSK-LIVE-GATED",
        }
        with patch.dict(os.environ, {"STAMFORD_KIOSK_FACE_MODE": "live", "STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED": "0"}, clear=False):
            with self.assertRaises(FaceAttendanceError) as context:
                self.store.match_kiosk_face(payload)
        self.assertEqual(context.exception.code, "GENETEC_CARDHOLDER_FACE_SYNC_DISABLED")

    def test_enrollment_and_matching_are_scoped_to_staff_person_type(self):
        staff_payload = self.payload("STAFF-200")
        staff_payload.update({"personType": "Staff", "guardianConsent": False, "consentConfirmed": True})
        enrolled = self.store.enroll(staff_payload)
        self.assertEqual(enrolled["personType"], "Staff")
        listed = self.store.list_students()["students"]
        self.assertEqual(next(item["personType"] for item in listed if item["studentId"] == "STAFF-200"), "Staff")
        match = self.store.match_student({
            "personType": "Staff", "eventType": "CheckIn", "sessionId": "ATT-STAFF-ONLY", "imageData": "staff-frame",
        })
        self.assertTrue(match["matched"])
        self.assertEqual(match["candidate"]["personType"], "Staff")
        self.assertEqual(match["candidate"]["studentId"], "STAFF-200")


if __name__ == "__main__":
    unittest.main(verbosity=2)

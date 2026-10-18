import csv
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        cls.bridge = (ROOT / "GenetecReadOnlyBridge" / "Program.cs").read_text(encoding="utf-8")
        with (ROOT / "docs" / "REQUIREMENTS-TRACEABILITY.csv").open(encoding="utf-8-sig", newline="") as source:
            cls.trace = list(csv.DictReader(source))

    def test_ui_uses_local_service_and_discloses_fallback(self):
        self.assertIn("const VMS_STATE_API='/api/v1/state'", self.ui)
        self.assertIn("async function startPrototype()", self.ui)
        self.assertIn("serverPersistence.suspended=true", self.ui)
        self.assertIn("SAVE CONFLICT", self.ui)
        self.assertIn("LOCAL FALLBACK", self.ui)
        self.assertIn("Predictable demo token", self.ui)
        self.assertIn("/api/v1/qr/check-in", (ROOT / "service" / "vms_service.py").read_text(encoding="utf-8"))

    def test_bridge_remains_loopback_and_guarded(self):
        self.assertIn('private const string Prefix = "http://127.0.0.1:5190/"', self.bridge)
        self.assertIn('"/api/genetec/visitor-lifecycle"', self.bridge)
        self.assertIn('"/api/genetec/visitor-lifecycle-state"', self.bridge)
        self.assertIn('"SYNC SYNTHETIC STAMFORD VMS VISITOR LIFECYCLE"', self.bridge)
        self.assertIn('VMS-SDKTEST-', self.bridge)
        self.assertIn('No lifecycle mutation was attempted.', self.bridge)

    def test_checkout_uses_verified_checkin_timestamp_not_archive_creation_timestamp(self):
        self.assertIn('DateTime canonicalCheckinUtc = existingCheckinUtc.Value;', self.bridge)
        self.assertIn('store.Events.Add(new LifecycleEventRecord', self.bridge)
        self.assertRegex(self.bridge, r'Math\.Abs\(\(existingCheckinUtc\.Value\s*-\s*verifiedLedgerCheckinUtc\.ToUniversalTime\(\)\)\.TotalSeconds\)\s*>\s*2')
        self.assertIn('savedCheckOutCheckinMatchesVerifiedCheckIn', self.bridge)
        self.assertIn('savedCheckOutMatchesArchive', self.bridge)

    def test_genetec_visit_window_normalization_handles_unicode_variants(self):
        domain = (ROOT / "service" / "domain.py").read_text(encoding="utf-8")
        self.assertIn("def canonicalize_window(raw: str)", domain)
        self.assertIn("WINDOW_IGNORABLE", domain)
        self.assertIn("WINDOW_DASHES", domain)
        self.assertIn('Normalize(NormalizationForm.FormKC)', self.bridge)
        self.assertIn(r"[\p{Pd}\u2212]", self.bridge)
        self.assertIn("normalizeGenetecVisitWindow(v.window)", self.ui)
        self.assertIn("VisitWindow:genetecWindow", self.ui)

    def test_secure_kiosk_checkin_checkout_creates_genetec_lifecycle_event(self):
        self.assertIn("if(v.genetecVisitorGuid){const lifecycleType=action==='checkin'?'CheckIn':'CheckOut';", self.ui)
        self.assertIn("createLifecycleEventRecord(v,lifecycleType,lifecycleTime||new Date().toISOString())", self.ui)
        self.assertIn("IsAllowedSyntheticLifecycleBookingId(bookingId)", self.bridge)
        self.assertIn(r"^VMS-UI-[0-9A-F]{6}$", self.bridge)
        self.assertIn("IsAllowedSyntheticLifecycleEventId(bookingId, eventId)", self.bridge)
        lifecycle_launcher = (ROOT / "GenetecReadOnlyBridge" / "Start-Bridge-VisitorLifecycleTest.cmd").read_text(encoding="utf-8")
        self.assertIn("-EnableVisitorSync -EnableVisitorLifecycleSync", lifecycle_launcher)
        self.assertNotIn("Read-Host", lifecycle_launcher)

    def test_qr_scanner_falls_back_to_loopback_decoder_without_native_api(self):
        self.assertIn("if('BarcodeDetector' in window)", self.ui)
        self.assertIn("/api/v1/qr/decode-frame", self.ui)
        self.assertIn("startQrCameraFromButton(action)", self.ui)
        self.assertIn("opencv-python-headless", (ROOT / "requirements-scanner.txt").read_text(encoding="utf-8"))
        service = (ROOT / "service" / "vms_service.py").read_text(encoding="utf-8")
        self.assertIn('if path == "/api/v1/qr/decode-frame":', service)
        self.assertIn("decode_qr_data_url(image_data)", service)

    def test_traceability_matrix_has_unique_110_srs_ids(self):
        ids = [row["Requirement ID"] for row in self.trace]
        self.assertEqual(len(ids), 110)
        self.assertEqual(len(set(ids)), len(ids))
        for prefix, end in [("VMS-INV", 9), ("VMS-VIS", 12), ("VMS-PRN", 4), ("VMS-CARD", 9), ("VMS-GRP", 9), ("VMS-UX", 8), ("INT-GEN", 10), ("AI-VID", 12), ("SEC", 14), ("DATA", 5), ("OPS", 6), ("NFR", 12)]:
            expected = {f"{prefix}-{i:03d}" for i in range(1, end + 1)}
            self.assertTrue(expected.issubset(set(ids)), f"Missing IDs for {prefix}")

    def test_launcher_starts_local_service_not_generic_file_server(self):
        launcher = (ROOT / "Start-Demo.cmd").read_text(encoding="utf-8")
        self.assertIn("service\\vms_service.py", launcher)
        self.assertIn("--host 127.0.0.1", launcher)
        self.assertNotIn("python -m http.server", launcher)

    def test_genetec_credentials_use_current_user_dpapi_not_repository_plaintext(self):
        store = (ROOT / "GenetecReadOnlyBridge" / "Genetec-CredentialStore.ps1").read_text(encoding="utf-8")
        launcher = (ROOT / "GenetecReadOnlyBridge" / "Start-Bridge.ps1").read_text(encoding="utf-8")
        save_script = (ROOT / "GenetecReadOnlyBridge" / "Save-GenetecCredentials.ps1").read_text(encoding="utf-8")
        self.assertIn('DataProtectionScope]::CurrentUser', store)
        self.assertIn('ProtectedData]::Protect', store)
        self.assertIn('ProtectedData]::Unprotect', store)
        self.assertIn('genetec-connection.dpapi', store)
        self.assertIn('Get-StamfordGenetecCredentialProfile', launcher)
        self.assertNotIn('Read-Host', launcher)  # bridge startup must never prompt
        self.assertNotIn('Read-StamfordGenetecCredentialProfileInteractive', launcher)
        self.assertIn('Run Save-GenetecCredentials.ps1 once', launcher)  # fail fast if one-time setup is missing
        self.assertIn('Save-StamfordGenetecCredentialProfile', save_script)
        self.assertNotIn('ConvertTo-SecureString', store)  # no plaintext-credential config import/export path
        self.assertNotIn('$profile =', launcher)  # don't overwrite PowerShell's automatic $PROFILE variable
        # Write scopes are available only through explicit non-interactive switches.
        self.assertIn('[switch]$EnableTestCardholderWrites', launcher)
        self.assertIn('[switch]$EnableVisitorSync', launcher)
        self.assertIn('[switch]$EnableVisitorLifecycleSync', launcher)
        self.assertIn("if ($EnableVisitorSync) { '1' } else { '0' }", launcher)
        self.assertIn('Start-Bridge.ps1', (ROOT / 'GenetecReadOnlyBridge' / 'Start-Bridge.cmd').read_text(encoding='utf-8'))
        self.assertNotIn('Read-Host', (ROOT / 'GenetecReadOnlyBridge' / 'Start-Bridge.cmd').read_text(encoding='utf-8'))
        visitor_launcher = (ROOT / 'GenetecReadOnlyBridge' / 'Start-Bridge-VisitorSync.cmd').read_text(encoding='utf-8')
        self.assertIn('-EnableVisitorSync', visitor_launcher)
        self.assertNotIn('Read-Host', visitor_launcher)
        self.assertIn('Remove-Item \"Env:$name\" -ErrorAction SilentlyContinue', launcher)

    def test_genetec_auto_start_and_startup_probe_are_wired(self):
        launcher = (ROOT / "Start-Demo.cmd").read_text(encoding="utf-8")
        auto_start = (ROOT / "GenetecReadOnlyBridge" / "Start-Bridge-Automatically.ps1").read_text(encoding="utf-8")
        self.assertIn("Start-Bridge-Automatically.ps1", launcher)
        self.assertIn("-EnableVisitorSync -EnableVisitorLifecycleSync", auto_start)
        self.assertIn("-WindowStyle Hidden", auto_start)
        self.assertIn("/api/genetec/status", auto_start)
        self.assertIn("genetec-connection.dpapi", auto_start)
        self.assertIn("dotnetCommand.Source build", auto_start)
        self.assertIn("Genetec bridge built automatically", auto_start)
        self.assertIn("$expectedBridgeBuild = '5.2.4'", auto_start)
        self.assertIn("Get-BridgeHealth", auto_start)
        self.assertIn("Stop-StalePrototypeBridge", auto_start)
        self.assertIn("Get-CimInstance Win32_Process -Filter", auto_start)
        self.assertIn("Stop-StalePrototypeBridge $health", auto_start)
        self.assertIn('bridgeBuild = BridgeBuild', self.bridge)
        self.assertIn('bridgeExecutablePath = Assembly.GetExecutingAssembly().Location', self.bridge)
        self.assertNotIn("Read-Host", auto_start)
        self.assertIn("probeBridge({silent:true})", self.ui)
        self.assertIn("id=\"genetecAutoTitle\"", self.ui)

    def test_visit_form_requires_explicit_start_and_end_times(self):
        self.assertIn('id="visitorVisitDate" min="${daysFromToday(0)}" value="${daysFromToday(0)}"', self.ui)
        self.assertIn('Visit date defaults to today in Singapore school time.', self.ui)
        self.assertIn("function amendVisitSchedule(id)", self.ui)
        self.assertIn("/api/v1/visits/${encodeURIComponent(id)}/amend", self.ui)
        self.assertIn("This pass is scheduled for ${info.visitDate}, ${info.visitWindow}", self.ui)
        self.assertIn('id="visitorStartTime" step="60"', self.ui)
        self.assertIn('id="visitorEndTime" step="60"', self.ui)
        self.assertIn('type="time"', self.ui)
        self.assertIn('Choose any hour and minute.', self.ui)
        self.assertIn("timeZone:'Asia/Singapore'", self.ui)
        self.assertIn('All dates and times use school time (Asia/Singapore, UTC+08:00).', self.ui)
        self.assertIn("if(endTime<=startTime)", self.ui)
        self.assertIn("This pass is scheduled for ${info.visitDate}, ${info.visitWindow}", self.ui)
        self.assertIn("const windowValue=`${startTime}–${endTime}`", self.ui)
        self.assertNotIn('id="visitorWindow"', self.ui)
        self.assertNotIn("Day 1", self.ui)
        self.assertNotIn("Day 2", self.ui)
        self.assertNotIn("Day 1 Prototype", self.ui)

    def test_genetec_invitation_sync_allows_unchecked_in_booking_during_open_window(self):
        # The invitation endpoint is pre-registration only: it must block expired visits,
        # but must not require the scheduled start to remain >1 minute in the future.
        self.assertIn('if (departureLocal <= bridgeNow)', self.bridge)
        self.assertIn('VISIT_WINDOW_EXPIRED', self.bridge)
        self.assertIn('keeps native activation at Never', self.bridge)
        self.assertNotIn('INVITED_ARRIVAL_NOT_FUTURE', self.bridge)
        self.assertNotIn('arrivalLocal <= bridgeNow.AddMinutes(1)', self.bridge)
        self.assertIn('Only an invited synthetic booking can be synchronised', self.bridge)
        self.assertIn('lifecycleAlreadyStarted', self.bridge)

    def test_kiosk_can_scan_qr_using_laptop_camera(self):
        self.assertIn("navigator.mediaDevices.getUserMedia", self.ui)
        self.assertIn("new BarcodeDetector({formats:['qr']})", self.ui)
        self.assertIn("function startQrCamera(action)", self.ui)
        self.assertIn("function scanQrCameraFrame()", self.ui)
        self.assertIn("await kioskScanCommit(action)", self.ui)
        self.assertIn("stopQrCamera(false)", self.ui)
        self.assertIn("function navigate(v){if(currentView==='kiosk'&&v!=='kiosk'){stopQrCamera(false);stopKioskFaceCamera(false);}", self.ui)

    def test_milestone_511_build_marker_and_stale_build_prevention(self):
        self.assertIn('<title>Stamford VMS | Visitor Management System · UI 5.2.4</title>', self.ui)
        self.assertNotIn('UI 5.2.0', self.ui)
        self.assertIn('meta name="vms-build" content="5.2.4"', self.ui)
        self.assertIn('UI BUILD 5.2.4', self.ui)
        self.assertIn("const VMS_BUILD='5.2.4'", self.ui)
        self.assertNotIn('Planned visit window', self.ui)
        self.assertNotIn('A USB QR scanner that types into a field', self.ui)
        launcher = (ROOT / "Start-Demo.cmd").read_text(encoding="utf-8")
        self.assertIn("BUILD 5.2.4", launcher)
        self.assertIn("PORT_IN_USE", launcher)
        self.assertIn("previous VMS instance", launcher)
        service = (ROOT / "service" / "vms_service.py").read_text(encoding="utf-8")
        self.assertIn('"build": "5.2.4"', service)
        self.assertIn("StamfordVMSPrototype/5.2.4", service)
        self.assertIn('Cache-Control", "no-store, no-cache, must-revalidate, max-age=0', service)

    def test_checkout_recovery_requires_readback_verified_checkin_first(self):
        # If the VMS has already recorded local arrival and departure, render the
        # unsynced original CheckIn as the next action; don't offer checkout first.
        self.assertIn("v.status==='Checked out' && checkInRecord && checkInRecord.state!=='Synced' && checkOutRecord", self.ui)
        self.assertIn("desiredEvent='CheckIn'", self.ui)
        self.assertIn('Sync prerequisite Genetec check-in first', self.ui)
        self.assertIn("v.genetecLifecycleCheckIn.state!=='Synced'", self.ui)
        self.assertIn('CHECKOUT IS BLOCKED UNTIL THE ORIGINAL CHECK-IN EVENT', self.ui.upper())
        self.assertIn("const historicalCheckInReplay=eventType==='CheckIn'&&v.status==='Checked out'", self.ui)
        self.assertIn('The original check-in is older than the bridge event window', self.ui)
        # Success rerenders the next event button (checkout); a finally block must
        # not accidentally disable it based on the just-completed check-in record.
        self.assertNotIn("retry.disabled=!(lastBridgeResponse.connected===true", self.ui)

    def test_face_attendance_is_opt_in_and_group_photo_enrollment_is_blocked(self):
        face = (ROOT / "service" / "face_attendance.py").read_text(encoding="utf-8")
        self.assertIn('STAMFORD_FACE_ATTENDANCE_ENABLED', face)
        self.assertIn('STAMFORD_BIOMETRIC_POLICY_APPROVED', face)
        self.assertIn('STAMFORD_GROUP_FACE_IDENTIFICATION_APPROVED', face)
        self.assertIn('GROUP_PHOTO_ENROLLMENT_BLOCKED', face)
        self.assertIn('groupImagePersisted', face)
        self.assertIn('humanConfirmationRequired', face)
        self.assertIn('CHECKIN_REQUIRED', face)
        self.assertIn('CryptProtectData', face)
        self.assertIn('CryptUnprotectData', face)
        ui = self.ui
        self.assertIn('id="view-attendance"', ui)
        self.assertIn('Student attendance', ui)
        self.assertIn('matchGroupFaces()', ui)
        self.assertIn('confirmGroupFaceAttendance()', ui)
        self.assertIn('studentId', ui)

    def test_genetec_bulk_student_roster_is_group_or_prefix_scoped_and_read_only(self):
        service = (ROOT / "service" / "vms_service.py").read_text(encoding="utf-8")
        face_service = (ROOT / "service" / "face_attendance.py").read_text(encoding="utf-8")
        self.assertIn("/api/genetec/student-roster", self.bridge)
        self.assertIn("/api/genetec/student-roster-groups", self.bridge)
        self.assertIn("externalIdPrefix", self.bridge)
        self.assertIn("groupNames = selectedGroups", self.bridge)
        self.assertIn("STUDENT_ROSTER_SCOPE_TOO_LARGE", self.bridge)
        self.assertIn("imageBytesReturned = false", self.bridge)
        self.assertIn("rawCredentialNumbersReturned = false", self.bridge)
        self.assertIn("/api/v1/genetec/student-roster/groups", service)
        self.assertIn("/api/v1/genetec/student-roster/import", service)
        self.assertIn("genetec_cardholder_roster", face_service)
        self.assertIn("staff-confirmed-genetec-portrait-review", face_service)
        self.assertIn("/api/v1/genetec/student-roster", self.ui)
        self.assertIn("Select only approved student-only groups", self.ui)
        # Directory query discovers the complete roster; group membership uses the SDK method, not guessed properties.
        self.assertIn("ReadConfiguredEntities<Cardholder>(engine, EntityType.Cardholder", self.bridge)
        self.assertIn("ReadConfiguredEntities<CardholderGroup>(engine, EntityType.CardholderGroup", self.bridge)
        self.assertIn('method.Name, "IsMemberOf"', self.bridge)
        self.assertIn("CARDHOLDER_GROUP_MEMBERSHIP_API_UNAVAILABLE", self.bridge)
        self.assertIn("Cardholder.IsMemberOf(CardholderGroup)", self.bridge)

    def test_genetec_cardholder_face_import_is_read_only_and_approval_gated(self):
        face = (ROOT / "service" / "face_attendance.py").read_text(encoding="utf-8")
        service = (ROOT / "service" / "vms_service.py").read_text(encoding="utf-8")
        project = (ROOT / "GenetecReadOnlyBridge" / "GenetecReadOnlyBridge.csproj").read_text(encoding="utf-8")
        self.assertIn('"/api/genetec/cardholders"', self.bridge)
        self.assertIn('"/api/genetec/cardholder-photo"', self.bridge)
        self.assertIn("cardholderGroups = ReadCardholderGroupNames(cardholder, engine, null)", self.bridge)
        self.assertIn("RequireCardholderFaceSyncApproval()", self.bridge)
        self.assertIn("MaskCredentialIdentifier(rawIdentifier)", self.bridge)
        self.assertIn("rawCredentialNumbersReturned = false", self.bridge)
        self.assertIn("STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED", face)
        self.assertIn("GENETEC_CARDHOLDER_FACE_SYNC_DISABLED", face)
        self.assertIn("/api/v1/face-attendance/genetec-cardholders", service)
        self.assertIn("/api/v1/face-attendance/enroll-from-genetec", service)
        self.assertIn("CryptProtectData", face)
        self.assertIn("genetec_cardholder_guid", face)
        self.assertIn("genetec_credential_refs_json", face)
        self.assertIn('Reference Include="System.Drawing"', project)
        self.assertIn("Read-only Genetec Cardholder import", self.ui)
        self.assertIn("enrollFaceStudentFromGenetec()", self.ui)

    def test_secure_qr_tokens_are_removed_from_browser_snapshot(self):
        self.assertIn('function demoStateSnapshot()', self.ui)
        self.assertIn('if(!v.securePass)return v;const copy={...v};delete copy.token;return copy;', self.ui)
        self.assertIn('token not persisted', self.ui)
        domain = (ROOT / "service" / "domain.py").read_text(encoding="utf-8")
        self.assertIn('secrets.token_urlsafe(32)', domain)
        self.assertIn('qr_token_hash TEXT UNIQUE', domain)
        self.assertIn('def token_digest(token: str)', domain)


if __name__ == "__main__":
    unittest.main(verbosity=2)

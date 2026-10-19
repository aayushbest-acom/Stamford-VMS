from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GenetecBridgeStartupScopeTests(unittest.TestCase):
    def test_bridge_scopes_are_command_line_options_not_feature_environment_flags(self):
        program = (ROOT / "GenetecReadOnlyBridge" / "Program.cs").read_text(encoding="utf-8")
        start = (ROOT / "GenetecReadOnlyBridge" / "Start-Bridge.ps1").read_text(encoding="utf-8")
        for env_flag in ("GENETEC_ENABLE_TEST_WRITES", "GENETEC_ENABLE_VISITOR_SYNC", "GENETEC_ENABLE_VISITOR_LIFECYCLE_SYNC"):
            self.assertNotIn(env_flag, program)
            self.assertNotIn(env_flag, start)
        for flag in ("--enable-test-cardholder-writes", "--enable-visitor-sync", "--enable-visitor-lifecycle-sync"):
            self.assertIn(flag, program)
            self.assertIn(flag, start)

    def test_auto_launcher_checks_bridge_scope_state_before_reuse(self):
        auto = (ROOT / "GenetecReadOnlyBridge" / "Start-Bridge-Automatically.ps1").read_text(encoding="utf-8")
        self.assertIn("$expectedBridgeBuild = '5.2.8'", auto)
        self.assertIn("$health.testCardholderWriteEnabled -eq $true", auto)
        self.assertIn("$health.visitorSyncEnabled -eq $true", auto)
        self.assertIn("$health.visitorLifecycleSyncEnabled -eq $true", auto)
        self.assertIn("-EnableTestCardholderWrites -EnableVisitorSync -EnableVisitorLifecycleSync", auto)

    def test_photo_lookup_requires_explicit_policy_switches_and_is_enabled_in_approved_auto_profile(self):
        program = (ROOT / "GenetecReadOnlyBridge" / "Program.cs").read_text(encoding="utf-8")
        start = (ROOT / "GenetecReadOnlyBridge" / "Start-Bridge.ps1").read_text(encoding="utf-8")
        auto = (ROOT / "GenetecReadOnlyBridge" / "Start-Bridge-Automatically.ps1").read_text(encoding="utf-8")
        for flag in ("--enable-cardholder-face-sync", "--confirm-biometric-policy-approved", "--confirm-genetec-cardholder-face-sync-approved"):
            self.assertIn(flag, program)
        self.assertIn("$allFaceOptions", start)
        self.assertIn("-EnableCardholderFaceSync -ConfirmBiometricPolicyApproved -ConfirmGenetecCardholderFaceSyncApproval", auto)
        self.assertIn("$health.cardholderFaceSyncEnabled -eq $true", auto)

    def test_cors_preflight_allows_csrf_header_and_keeps_origin_restricted(self):
        program = (ROOT / "GenetecReadOnlyBridge" / "Program.cs").read_text(encoding="utf-8")
        self.assertIn('response.Headers["Access-Control-Allow-Headers"] = "Content-Type, X-Stamford-CSRF";', program)
        self.assertIn('String.Equals(origin, "http://127.0.0.1:8080", StringComparison.OrdinalIgnoreCase)', program)
        self.assertIn('String.Equals(origin, "http://localhost:8080", StringComparison.OrdinalIgnoreCase)', program)
        self.assertNotIn('Access-Control-Allow-Headers"] = "*"', program)

    def test_get_on_post_only_visitor_booking_route_reports_all_write_scope_state(self):
        program = (ROOT / "GenetecReadOnlyBridge" / "Program.cs").read_text(encoding="utf-8")
        self.assertIn('error = "not_found", writeOperationsEnabled = IsAnyWriteScopeEnabled()', program)
        self.assertIn("private static bool IsAnyWriteScopeEnabled()", program)


if __name__ == "__main__":
    unittest.main()

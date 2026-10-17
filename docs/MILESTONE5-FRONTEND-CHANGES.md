# Milestone 5 — Front-end workflow improvements

## Implemented in this iteration

- `Start-Demo.cmd` invokes a non-interactive Genetec connection bootstrap before starting the local VMS. If a bridge is already listening, the helper reuses it rather than creating a duplicate. If prerequisites are missing or Genetec cannot be verified, the VMS still opens and displays the connection state.
- The auto-start helper launches the local bridge hidden and redirects stdout/stderr to `%LOCALAPPDATA%\StamfordVMS\Logs`. When the bridge is not already running and the DPAPI profile/executable exist, it enables the existing guarded visitor-booking and synthetic visitor-lifecycle scopes. Actual mutation routes still enforce allowlists and request-level confirmations. Use only a non-production Genetec Directory with synthetic records.
- On page startup, the UI automatically probes the live Genetec status/capabilities and updates an overview connection banner; users no longer need to open the diagnostics page merely to make the connection state known. The diagnostics page remains available for troubleshooting.
- Removed user-facing `Day 1` / `Day 2` wording. Replaced the banner with connection status and renamed the Genetec navigation item to `Genetec connection`.
- Visitor invitations now require separate explicit start/end time inputs, build a normalized visit-window string, and reject end times that are not later than start time.
- Kiosk QR scanning now requests the laptop camera, uses the browser's native `BarcodeDetector` QR reader where supported, submits the decoded token to the existing server validation/check-in/check-out flow, and releases the camera on reset or navigation away. Manual token entry remains available as a fallback. Use current Microsoft Edge/Chrome and allow camera access for `http://127.0.0.1:8080`.

## Architecture note

The browser does not directly load the Genetec SDK. The supported integration path still uses the local loopback bridge, but the bootstrap starts it automatically in the background and the UI checks its status on startup. A live Genetec session, SDK compatibility and required scopes still need on-host verification.

## Validation

- Existing Python/API and repository-contract regression tests plus new front-end contract tests.
- JavaScript syntax check with Node.js.
- Static check of launcher wiring and front-end requirements.
- Camera permission, real QR decoding, PowerShell DPAPI startup, and live Genetec round-trip must be tested on the target Windows workstation; they cannot be truthfully certified by static tests alone.

## Safety boundary

This remains an unauthenticated loopback prototype. The automatic bridge launcher enables only the existing guarded synthetic visitor scopes and must not be used with production data or a production Genetec Directory until authentication, role permissions, approval policy, and formal environment separation are completed.

# Milestone 5.1.1 — Frontend re-delivery and stale-build protection

This is a corrective re-package after screenshots showed an older UI still running. The previous Milestone 5 source already contained the requested form and camera scanner implementation, but the screenshots clearly came from a stale/older served build. This build makes the active version visible and prevents silently attaching to an older service on port 8080.

## Verify in the running UI
- Sidebar footer says `UI BUILD 5.1.1`.
- Create invitation has separate required `Start time` and `End time` inputs; it does not contain a prefilled visit-window selector.
- Self-service kiosk displays `CAMERA QR SCANNER`, a live camera preview area, and Start/Stop camera controls; manual token entry is only under a disclosure as fallback.
- There is no Day 1 / Day 2 banner.
- Startup console says `BUILD 5.1.1` and prints the extracted launch folder.
- Static responses include no-cache headers so browser caching should not mask a file update.

## Run the correct build
1. Stop the old VMS console with Ctrl+C. Do not leave the older Milestone 3/4/5 demo server listening on port 8080.
2. Extract this ZIP to a new folder, for example `StamfordVMS-Milestone5.1.1`.
3. Start `Start-Demo.cmd` from that new extracted folder.
4. Confirm the console says `BUILD 5.1.1` and the displayed launch folder is the new directory.
5. Open `http://127.0.0.1:8080` and confirm the sidebar says `UI BUILD 5.1.1`. If the launcher's port preflight reports an existing service, stop the previous VMS console before retrying.

## Notes
The camera scanner uses `navigator.mediaDevices.getUserMedia` and the browser's native `BarcodeDetector` API. Use a recent Microsoft Edge or Chrome on `http://127.0.0.1:8080`, allow camera permission, and ensure another application is not holding the camera. Manual entry remains available as a fallback.

The Genetec bridge auto-start logic and saved Windows DPAPI credential profile are retained. This remains a prototype with no application authentication; synthetic data only.

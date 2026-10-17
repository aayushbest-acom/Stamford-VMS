# Milestone 5.1.2 — QR camera scanner compatibility fix

## Root cause
The previous kiosk used only the browser-native `BarcodeDetector` API. In the user's Microsoft Edge build, `getUserMedia` is available but `BarcodeDetector` is absent. `startQrCamera()` threw before opening the webcam; button clicks attached no rejection handler, producing repeated uncaught promise errors.

## Change
- Native browser QR detection is used when supported.
- When unavailable, the kiosk downsizes a camera frame to at most 640 px width and sends a JPEG data URL to `POST /api/v1/qr/decode-frame` on `127.0.0.1:8080`.
- The local Python service decodes via OpenCV's `QRCodeDetector` and returns only detected QR text. Image frames are not persisted or logged.
- `Start-Demo.cmd` checks whether Python can import `cv2`; if not, it silently attempts a one-time install from `requirements-scanner.txt` after confirming port 8080 is available. Internet access is needed for that initial install. If installation fails, VMS still starts, camera scanning shows an actionable message, and manual token entry remains available.
- Camera button errors are caught and displayed in the kiosk instead of generating uncaught promise exceptions. If no decoder is available, the scan animation loop stops instead of spinning needlessly.

## Validation
- Automated regression suite includes local QR decoding of a generated JPEG and a full POST endpoint test.
- Python compile checks and inline JavaScript syntax check pass locally.
- Target laptop webcam behavior must still be verified in Edge with camera permission granted.

## Boundary
This remains loopback prototype code without application authentication. QR frames remain local but the endpoint is not an authenticated production endpoint. Use synthetic QR passes only.

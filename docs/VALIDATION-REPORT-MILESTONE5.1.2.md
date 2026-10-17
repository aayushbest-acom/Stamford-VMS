# Stamford VMS — Milestone 5.1.2 validation report

**Scope:** Fix camera scanner failure when Microsoft Edge lacks `BarcodeDetector` by providing a loopback server-side OpenCV fallback; handle start-camera failures without uncaught promises; keep the no-Day-1/Day-2 UI, explicit start/end time fields, and Genetec auto-start behavior.

## Results

| Check | Result | Notes |
|---|---|---|
| Python automated tests | **PASS (35/35)** | Includes QR decoder unit tests, local API frame-decoding integration, token validation lifecycle, and source contracts. |
| Python compileall | **PASS** | `python -m compileall -q service tests`. |
| Inline UI JavaScript syntax | **PASS** | Extracted inline script checked with Node.js `--check`. |
| Synthetic QR JPEG decoded by OpenCV | **PASS** | Decoder returned exact synthetic token from a generated JPEG frame. |
| Loopback `/api/v1/qr/decode-frame` integration | **PASS** | HTTP route decoded QR text in memory; request body is not logged. |
| Windows OpenCV auto-install | **NOT RUN** | Requires target Windows/Python environment; it runs only if `import cv2` fails. |
| Actual Edge webcam and permissions | **NOT RUN** | Must verify with user's laptop camera. |
| Live Genetec SDK build and lifecycle round-trip | **NOT RUN** | Unchanged from previous milestone; must use the target Windows SDK host. |

## Run checks

```powershell
python -m unittest discover -s tests -v
python -m compileall -q service tests
```

## Target workstation verification

1. Close the old VMS and extract this release into a fresh directory.
2. Launch `Start-Demo.cmd`. If the current Python lacks OpenCV, the launcher attempts a one-time pip install of `opencv-python-headless`.
3. Confirm UI build `5.1.2` appears, open kiosk check-in, allow camera access, and scan a generated synthetic visitor QR.
4. Verify a random/invalid QR is rejected and a valid unexpired pass causes one server-side check-in, with an idempotent retry not duplicating it.
5. Repeat the test for checkout.

## Safety

Loopback prototype only. Application authentication/RBAC is not configured; use synthetic data only and do not expose to LAN/Internet. Raw QR tokens are only used for a single local API transaction; camera frames are not stored or logged.

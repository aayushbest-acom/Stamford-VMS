# Validation report — Build 5.2.3 integration adapters

Date: 18 October 2026 (development environment run)

## Scope implemented

- New `service/integrations.py`: local mock SMS outbox plus explicit Vonage and Twilio live adapters.
- SMS safety controls: E.164 validation, 1,024-character body cap, exact recipient allow-list for live sending, persistent idempotency keys, masked recipient at rest, live body hashed rather than retained, and no automatic retry when provider outcome is unknown.
- Invitation link fail-closed behaviour: mock mode uses a clearly labelled local-demo note; live invitation sending requires an opaque pass token and an HTTPS pass URL template. The adapter validates template syntax only; the hosted endpoint must be built, approved and independently tested before live invitation SMS is enabled.
- `GET /api/v1/integrations/status`, `GET /api/v1/integrations/sms/outbox`, and `POST /api/v1/integrations/sms/send`.
- Integrations & devices UI for SMS status/test/outbox, live webcam preview and in-memory frame capture, and browser-to-Windows native print-dialog test.
- Documentation in `docs/INTEGRATIONS-SETUP.md`; README milestone text now reflects 5.2.3; traceability CSV updated for the requirements affected by this slice.

## Validation executed

- Full Python unit suite: **81 passed, 0 failed** (including 12 integration-adapter tests).
- Python compilation: `service/integrations.py` and `service/vms_service.py` passed.
- Inline JavaScript syntax check: `node --check` passed.
- Runtime HTTP smoke test: local service started on loopback; integration status returned; mock SMS returned `Simulated`; outbox persistence/readback succeeded; UI static serving contained the new integration navigation.
- `git diff --check`: passed after removing trailing whitespace from the touched UI line.

## Browser test limitation

A Chromium Playwright attempt was blocked before page navigation by the environment (`net::ERR_BLOCKED_BY_ADMINISTRATOR` against loopback). The HTTP smoke test succeeded; this is an environment-level browser restriction, not proof of a UI defect. A supported browser run is still needed on the Windows demo host.

## Not verified by this development run

- No SMS provider credentials or handset delivery were available. Provider API request/response handling is tested with mocked HTTP responses only.
- Windows native print dialog and physical label output were not testable in this host environment. The implementation is browser `window.print()`, not direct spooler automation; confirm output on the actual Windows printer.
- Webcam preview/capture was implemented in browser code but could not be verified against the user's physical webcam here.
- No real guest-facing HTTPS QR pass page is implemented. Live invitation SMS is blocked until an approved public/staging pass endpoint validates token expiry/revocation securely; setting the URL template alone does not create that endpoint.
- Existing Genetec bridge code is unchanged by this adapter slice. Security Center version/licensing, native visitor lifecycle, credential/access-window mapping, controller denial, and recovery must be tested on the approved Windows/Genetec host.
- Webcam preview is not the SRS's local AI inference pipeline. Detector selection, camera/time evidence routing and false-alert/missed-event measurements remain unimplemented/unvalidated.
- Application authentication/RBAC remains a P0 production showstopper. Keep the app loopback-only and use synthetic data only.

## Recommended next implementation slice

1. Windows-host end-to-end check: live browser camera, print test to the selected printer, and one live SMS to a verified allow-listed test recipient.
2. Implement the approved guest pass endpoint and integration route before sending real invitation links.
3. Finish visitor invitation/arrival/departure notification automation and print error/fallback behavior.
4. Implement replacement-card and group-visit workflows against explicit domain state machines and acceptance tests.
5. Validate the live Genetec lifecycle on the target host, including status read-back and physical-controller negative tests.
6. Implement approved local camera event ingestion/detector adapters only after feed, hardware, privacy, and pilot criteria are confirmed.
7. Add authentication/RBAC before use with real identities or network exposure.

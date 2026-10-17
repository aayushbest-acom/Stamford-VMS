# Milestone 4 — Visitor window and Genetec lifecycle wiring

**Date:** 16 October 2026  
**Disposition:** Code changes and local automated checks complete; target-host Genetec acceptance is still pending.

## Changes in this milestone

1. **Visit-window normalization:** The browser canonicalizes date-window separator variants (hyphen-minus, Unicode hyphens/dashes/minus, full-width punctuation and invisible formatting characters) to a stable `HH:MM-HH:MM` request value before a native Visitor sync. The Python domain API applies Unicode normalization and stores the canonical `HH:MM–HH:MM` representation. The Genetec bridge normalizes Unicode punctuation/spacing again before parsing, so it does not depend on one visually equivalent separator code point.
2. **Clearer validation:** The UI rejects malformed clock times before issuing a Genetec write. The bridge still validates visit date, non-expired appointment window, expiry, window ordering and the maximum allowed duration.
3. **UI-linked lifecycle path:** After a successful secure QR check-in/out, if the invitation has a verified Genetec Visitor GUID, the UI creates a stable lifecycle event record using the timestamp returned by the server-side domain API. Genetec lifecycle sync remains a separate explicit action and only counts as successful when the bridge returns native read-back verification.
4. **Narrow synthetic ID guard:** The bridge now permits lifecycle writes for the existing `VMS-SDKTEST-*` test harness pattern and the prototype's generated `VMS-UI-` plus six uppercase hexadecimal characters. It rejects arbitrary `VMS-*` IDs. Lifecycle event IDs must begin with the exact booking ID and a stable suffix. The lifecycle startup opt-in, request confirmation, version/licence/privilege checks and no-credential/no-access-right behavior remain.
5. **Non-interactive lifecycle test launcher:** Added `GenetecReadOnlyBridge/Start-Bridge-VisitorLifecycleTest.cmd`, which explicitly enables both guarded visitor-upsert and lifecycle scopes without a startup prompt. It is for synthetic test bookings in an approved non-production Directory only. `Start-Bridge-VisitorSync.cmd` still enables invited Visitor create/update only.
6. **Regression coverage:** Added tests for Unicode window normalization, invalid clock values, secure-kiosk lifecycle event recording, strict synthetic ID validation contracts and the new launcher.

## Validation performed here

- Python automated tests: **26/26 PASS** (`python -m unittest discover -s tests -v`).
- Python `compileall`: **PASS**.
- Inline browser JavaScript `node --check`: **PASS**.
- JavaScript window normalizer: **5 valid variants accepted and canonicalized; 3 invalid values rejected**.
- Traceability matrix: **110 unique requirement IDs retained**.
- C#/.NET bridge build, PowerShell execution, DPAPI behavior, interactive browser UAT and live Genetec SDK read-back: **NOT RUN in this environment**.

## Target-host retest needed

1. Stop the current bridge process with Ctrl+C.
2. Extract this repository update to a fresh folder; the DPAPI profile lives outside the repository and should remain available to the same Windows user on the same machine.
3. Run `dotnet clean` and `dotnet build` inside `GenetecReadOnlyBridge` on the workstation with the Genetec Security Center 5.11 SDK installed.
4. Start `Start-Bridge-VisitorSync.cmd` and retry invitation sync with a fresh, synthetic booking. Confirm the UI now sends the normalized ASCII-hyphen window and native schedule read-back passes.
5. For a full lifecycle smoke test, use `Start-Bridge-VisitorLifecycleTest.cmd` **only** in an approved non-production Directory, then create/sync a fresh synthetic booking with a visit window that is currently open (the local QR API permits check-in from 30 minutes before the start through 60 minutes after the planned end). Scan/check in, explicitly sync lifecycle, check out, explicitly sync lifecycle, and inspect the read-only state/ledger. Do not reuse a previously checked-out or unresolved booking.
6. Verify `lifecycleOperationMappingVerified`, the same native Visitor GUID, both event IDs, canonical check-in timestamp, and archived checkout read-back. If an operation times out or returns an uncertain result, inspect Genetec/read-only lifecycle state before retrying. Never repeat a potentially committed write under a new event ID.

## Remaining non-M4 acceptance blockers

This is still a prototype without application authentication/RBAC. The full production scope still needs approved role mapping, tamper-resistant audit, notifications/SMS, sticker printer integration, overdue escalation/reconciliation, card-replacement credential lifecycle, group photo capture/roster reconciliation, real CCTV inference and validated detectors, retention/egress/security design, backup/recovery, load/device testing and stakeholder sign-off. Do not use real student or visitor data or expose the services beyond loopback.

# Genetec developer startup scopes (Bridge 5.2.5)

## What changed

The bridge no longer uses `GENETEC_ENABLE_TEST_WRITES`, `GENETEC_ENABLE_VISITOR_SYNC`, or `GENETEC_ENABLE_VISITOR_LIFECYCLE_SYNC` environment variables to select write scopes. `Start-Bridge.ps1` passes explicit command-line options to the bridge process. The automatic VMS launcher enables three guarded scopes and refuses to reuse a connected bridge unless its health endpoint reports those scopes as active.

- `--enable-test-cardholder-writes`: creates/finds only the fixed synthetic test Cardholder, no credentials or access rights.
- `--enable-visitor-sync`: creates/updates a single invited VMS visitor booking with a stable VMS key; does not create credentials or change access rights.
- `--enable-visitor-lifecycle-sync`: enables the guarded native check-in/out candidate path for strict synthetic booking/event IDs only.
- Cardholder photo/credential-reference reads are enabled in the automatic developer startup by the explicit switches `-EnableCardholderFaceSync -ConfirmBiometricPolicyApproved -ConfirmGenetecCardholderFaceSyncApproval`, based on the school approval confirmed for this deployment. These switches are not a substitute for verifying the approved purpose, population and required person-specific consent/notice process. The bridge still serves the data only through its existing gated, read-only endpoints.

## Why the bridge must restart

Feature scopes are initialized once when the bridge process starts. Calling the launcher again while the old listener is still on `127.0.0.1:5190` cannot change the old process's flags. The startup probe now checks both build `5.2.5` and the required scope values—including Cardholder photo lookup—from `/api/health`; if either the build or scopes are stale, it stops only the recognized `GenetecReadOnlyBridge.exe` process and restarts it. If the launcher cannot identify the owning executable safely, it refuses to kill an unrelated process and asks for manual shutdown.

## Validation steps

1. Stop the old bridge or run `Start-Demo.cmd` and let the auto launcher detect the stale 5.2.4 bridge.
2. Confirm `/api/health` reports `bridgeBuild=5.2.5`, `testCardholderWriteEnabled=true`, `visitorSyncEnabled=true`, and `visitorLifecycleSyncEnabled=true`, and `cardholderFaceSyncEnabled=true`.
3. Confirm `/api/genetec/status` reports `connected=true` and `visitorManagementLicensed=true`.
4. Check the POST-only endpoint using the provided test script or actual VMS button. Typing `/api/genetec/visitor-booking` in a browser sends GET, so `not_found` is expected; use `/api/genetec/status` or `/api/genetec/capabilities` for status.
5. Start with synthetic visitor names/IDs and inspect the read-only booking lookup after any ambiguous result before retrying.

## Safety boundary

This bridge is connected to the Directory configured in the user's DPAPI profile. A developer workstation does not make the remote Directory a sandbox. The enabled write scopes do not create credentials or change access rights, but native Visitor records and check-in/out states do change in the connected Directory. Do not use a production Directory for synthetic lifecycle testing. Cardholder photo/credential-reference lookup is enabled in this approved developer profile via explicit startup acknowledgements. Confirm that the approval and applicable notices/consents cover the actual Cardholder population queried; the setting does not create biometric templates or write to Cardholder records.

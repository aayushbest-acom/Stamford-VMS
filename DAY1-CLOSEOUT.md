# Stamford VMS Prototype — Day 1 close-out

## Confirmed from the Windows host evidence supplied

- Frontend prototype is running and displaying the Stamford VMS workspace.
- Local bridge is listening at `http://127.0.0.1:5190/`.
- The bridge has authenticated to Security Center; Directory response in the UI is `5.11.3130.13`.
- SDK assembly version shown is `5.11.0.0`.
- Cardholder-group query returned 11 rows and `queryStatus: success`.
- Visitor Management is reported as licensed.
- The bridge was started with the guarded native Visitor sync opt-in enabled; the separate test-cardholder write remains disabled.

The observations above prove the read-only integration gate from the user-provided screenshot/output. They do **not** prove that a Visitor create/update operation has succeeded.

## Remaining Day 1 live-write proof

1. Build the bridge on the Windows SDK host after extracting this package.
2. Run `Test-BridgeReadOnly.ps1` to capture a repeatable read-only gate result.
3. Open the VMS UI and select **Genetec bridge → Test local bridge**. The Day 1 integration gate should update from Pending to Verified based on the response.
4. On an approved non-production instance only, use one invited synthetic booking and select **Sync invited booking to Genetec**, or run `Test-NativeVisitorSync.ps1`.
5. Confirm the operation returns a Visitor GUID and `serverReportReadbackVerified`, `nameReadbackVerified`, and `scheduleReadbackVerified` are all true. Inspect the created/updated entity in Security Center.
6. Repeat the same booking and confirm the same Visitor GUID is returned; then validate the update operation also keeps the same GUID.

Do not report the write milestone complete until those responses are captured. The bridge must be checked in Genetec before retrying any operation that times out or fails read-back.

## Package refinements in this close-out

- Day 1 UI gate reflects live connection, Directory version and query status instead of remaining static `Pending`.
- Write-operation summary distinguishes the fixed Cardholder test operation from native Visitor sync opt-in.
- PowerShell launcher/test scripts use ASCII-safe output/time strings for Windows PowerShell 5.1, avoiding UTF-8-no-BOM mojibake in the console.
- Launcher cleanup removes prompt variables and connection environment variables when the bridge exits.
- Added a read-only smoke test script that makes no writes.

## Security note

The latest console output pasted into the conversation contained a certificate/key-like value and used an administrator-named account. If the pasted value is active, rotate it. For ongoing testing, use a dedicated least-privilege integration account, keep secrets out of transcripts/screenshots, and use only an approved non-production Directory for writes.

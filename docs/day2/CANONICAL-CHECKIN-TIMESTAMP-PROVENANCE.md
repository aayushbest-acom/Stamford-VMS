# Canonical check-in timestamp provenance

## Why this correction is needed

On the tested Directory, an archived Visitor's raw `CheckinDate` may reflect entity creation time rather than the later activation-based check-in. The `VMS-SDKTEST-07` test completed the native check-in and checkout operations, but its final read-only output showed archive `CheckinDate=2026-10-16T05:33:01Z` while the saved check-in event's run ID was around `05:53:14Z`.

The previous normal checkout handler reused the archive's raw `CheckinDate` as the checkout event's `GenetecCheckinDateUtc`. That made the checkout event's canonical check-in field disagree with the previously read-back-verified CheckIn event even though `Visitor.Checkout()` and duplicate retries passed.

## Correction behavior

- Require one prior read-back-verified CheckIn ledger event for the same Visitor GUID.
- Compare its saved native check-in timestamp to the active Visitor's `Status.ActivationDate` read-back within two seconds before invoking checkout. A mismatch blocks checkout before mutation.
- Record the checkout event using that verified canonical check-in timestamp and the native archived checkout timestamp.
- The read-only lifecycle state reports `savedCheckOutGenetecCheckinDateUtc`, `savedCheckOutGenetecCheckoutDateUtc`, `savedCheckOutCheckinMatchesVerifiedCheckIn`, and `savedCheckOutMatchesArchive`.
- A consistent existing checkout yields a no-action/pass message. An absent event prompts local-ledger reconciliation only when archived state and saved verified check-in evidence support it. An existing event with mismatched evidence is flagged for manual ledger review; checkout is never repeated automatically.

## Safe validation sequence

1. Apply the patch and rebuild `GenetecReadOnlyBridge.csproj` on the licensed Windows host.
2. Run `Test-NativeVisitorLifecycleState.ps1 -BookingId VMS-SDKTEST-07`. This is read-only. Do not check in/out this booking again; a mismatch should be retained as a diagnostic and reviewed manually.
3. Confirm an unused fresh synthetic ID has zero active lookup matches, then create the future appointment using `Test-NativeVisitorSync.ps1 -BookingId VMS-SDKTEST-08 -PrepareForLifecycleTest -LifecycleLeadMinutes 5` (or another unused ID). The safe default is still 20 minutes; the 5-minute option is for a supervised rapid retest and must not be reduced below 5.
4. Run `Test-NativeVisitorLifecycle.ps1` for that fresh booking, then its read-only state script. Acceptance requires archived `CheckedOut`, no active Visitor, both event IDs present, `savedCheckOutCheckinMatchesVerifiedCheckIn=true`, and `savedCheckOutMatchesArchive=true`.
5. If any request fails or final consistency flag is false, stop. Do not replay lifecycle writes against an already checked-out booking.

The patch must be built and the fresh lifecycle test rerun on the target host before this correction can be marked verified. The browser UI-to-bridge acceptance test remains separate and outstanding.

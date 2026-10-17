# Reconcile a Genetec checkout that succeeded but was not written to the VMS ledger

## Why this exists

A checkout request can succeed in Genetec and archive the Visitor while the bridge later fails its read-back. In that case, the Genetec state is authoritative evidence that the visit is `CheckedOut`, but the local bridge ledger may contain the check-in event and no checkout event.

The bridge must **not** call `Visitor.Checkout()` again to repair this bookkeeping gap.

## Preconditions

The reconciliation endpoint is restricted to synthetic `VMS-SDKTEST-*` bookings and requires lifecycle sync to be explicitly enabled at bridge startup. Before a ledger change, it rechecks the Genetec Directory and archive and requires:

- no active Visitor matches the booking key;
- the local ledger has exactly one previously verified check-in event matching the mapped Visitor GUID;
- the archived native visit state is exactly `CheckedOut`;
- Genetec provides a valid checkout timestamp later than the saved check-in event;
- there is no conflicting checkout ledger entry.

The endpoint does not issue any Genetec mutation. It adds a local `CheckOut` event using the verified archived checkout timestamp, marks the event as reconciled from archive evidence, and returns the generated event ID. It is idempotent: a repeated reconciliation sees the already-recorded checkout and does not add another entry.

## Run it

1. Run `Test-NativeVisitorLifecycleState.ps1 -BookingId VMS-SDKTEST-06` and inspect its output.
2. Confirm `activeVisitorFound=false`, `archivedVisitorFound=true`, `visitState=CheckedOut`, a non-empty `savedCheckInEventId`, and an empty `savedCheckOutEventId`.
3. Run `Reconcile-NativeVisitorCheckout.ps1 -BookingId VMS-SDKTEST-06`.
4. Enter the exact phrase shown by the script only after confirming the read-only evidence.
5. The script calls a ledger-only POST and then re-runs the read-only state query. Confirm that `savedCheckOutEventId` is populated, `savedCheckOutMatchesArchive=true`, `savedCheckOutReconciledFromArchive=true`, and Genetec still reports the archived visit as `CheckedOut`.

If a checkout event is already present but `savedCheckOutMatchesArchive=false`, this missing-event reconciliation is not the right repair path. The script stops without mutating either Genetec or the local ledger; inspect and repair the conflicting local event under operator control.

If any precondition fails, stop and reconcile manually with an authorised Genetec administrator. Do not create a replacement Visitor for the same booking and do not repeat checkout.

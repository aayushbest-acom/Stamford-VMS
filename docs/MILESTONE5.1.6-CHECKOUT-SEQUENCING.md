# Build 5.1.6 — lifecycle checkout recovery

## Observed problem
The UI displayed Check-in `Pending` and Check-out `Failed`. The bridge rejected CheckOut because its durable ledger did not contain a successfully read-back-verified CheckIn for the booking. This rejection is correct: Genetec checkout must not be recorded before a matching native check-in is verified. The UX gap was that the visit-details view selected CheckOut solely from the final VMS status, hiding the pending CheckIn operation.

## Changes
- For a checked-out VMS visit where both local lifecycle records exist and CheckIn is not Synced, the UI now selects the original CheckIn event first and explains the required sequence.
- The sync method permits a historical CheckIn operation only when the VMS already has both original events, the checkout timestamp is later than the check-in timestamp, and the original check-in is within 30 minutes of the current clock. The original EventId/UTC time are preserved.
- The bridge request still identifies the operation as a CheckIn with `VmsStatus=Inside`. It does not claim the visit is checked out until the separate original checkout event is independently read-back verified.
- Once CheckIn returns `Synced`, opening details selects the original CheckOut for retry. A guard also blocks CheckOut submissions before CheckIn is Synced.
- Removed stale finally-block button enabling logic, which could have disabled the newly rendered checkout button based on the just-completed check-in record.

## Operator recovery
1. Stop the prior VMS/bridge console, unpack Build 5.1.6 into a new folder and run `Start-Demo.cmd`. Check UI and bridge build IDs.
2. Open the existing synthetic visit's Details. If local CheckIn is Pending/Failed and local CheckOut is Failed, select **Sync prerequisite Genetec check-in first**.
3. Confirm only in the approved non-production Directory, then require an independent native read-back result and `Check-in: Synced`.
4. Reopen visit details and retry the original checkout. Require its independent archived `CheckedOut` read-back.
5. If the check-in event is over 30 minutes old, timestamps are out of order, or Genetec indicates an existing/ambiguous activation, stop and inspect the read-only lifecycle-state endpoint. Do not replace timestamps or repeat uncertain mutations.

## Validation boundary
Local automated tests cover UI sequencing contracts, domain API and existing repository safety contracts. This environment cannot build against the licensed Windows Genetec SDK; target-host compilation and a fresh synthetic CheckIn/CheckOut round-trip remain required.

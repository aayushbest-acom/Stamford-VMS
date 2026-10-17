# Incident: active Visitor read-back and invalid appointment window

**Status:** Safe correction prepared; target Windows build and live retest required.

## Observed evidence

For `VMS-SDKTEST-05`, the read-only bridge lookup returned:

- `activationType=Never` and no activation date.
- `checkinDateUtc` from the previous implementation equal to the Visitor Creation date.
- Expected arrival at 23:25 and expiry/departure at 00:35 the next day, while the record was created at 23:35.

The supplied Security Desk screenshot simultaneously shows that the **Check in** action is available and displays the red `The time range is invalid` message. Therefore the old `Visitor.CheckinDate` value is not sufficient evidence of a real check-in for this active Visitor. The scheduled arrival was also already in the past when the test fixture created the record.

Genetec's user guide states that a visitor's activation date corresponds to check-in time and that `Never` activation is used when the visitor will be checked in manually later ([Genetec guide](https://techdocs.genetec.com/r/en-US/Security-Center-User-Guide-5.12/Checking-in-new-visitors)). The supplied SDK XML documents `Visitor.Arrival` as the activation date/time, `Visitor.VisitDate` as the scheduled visit date/time, `Visitor.Departure` as expiry, and `Visitor.CheckinDate` as check-in date/time.

## Current correction candidate

- `Visitor.VisitDate` stores the planned arrival.
- `Visitor.Status.Activate(DateTime.MinValue, departureLocal)` sets activation to `Never` and the expiration interval; `Visitor.Departure` is explicitly set to the scheduled departure.
- For an active Visitor, the bridge now treats `Status.ActivationType=Never` with no `ActivationDate` as `pre-registered`. `SpecificDate` with an activation date is the canonical `checked-in` signal. Unknown combinations return `ambiguous` and block writes.
- The raw `Visitor.CheckinDate` property is exposed separately as `rawEntityCheckinDateUtc`, not used alone as the active attendance state. The normalised `checkinDateUtc` field is derived from activation state.
- A lifecycle check-in uses `Visitor.Status.Activate(actualEventTime)`, with `Visitor.Arrival` set to the same timestamp, and requires a fresh-session activation-state read-back.
- `Test-NativeVisitorSync.ps1 -PrepareForLifecycleTest` creates an appointment starting two minutes in the future. The bridge serializes local appointment fields as local ISO timestamps (rather than UTC strings mislabeled as local), and the lifecycle test parses the offset via `DateTimeOffset`, waits until the expected arrival, and repeats the read-only unique-GUID/state lookup immediately before asking for write confirmation.

## Recovery and retest

1. Do not send more writes to `VMS-SDKTEST-05`. Ask an authorised Genetec administrator to inspect and reconcile the record with the invalid time-range state. Do not automatically delete or reset it.
2. Stop the running bridge and apply the current status-based read-back patch. Build against the installed Genetec 5.11 SDK on the Windows host.
3. Start the bridge against the approved non-production Directory. Enable native Visitor sync and lifecycle writes only for the designated synthetic test.
4. Use the fresh booking key `VMS-SDKTEST-06`:

   ```powershell
   .\Test-NativeVisitorSync.ps1 -BookingId VMS-SDKTEST-06 -PrepareForLifecycleTest
   .\Test-NativeVisitorLookup.ps1 -BookingId VMS-SDKTEST-06
   .\Test-NativeVisitorLifecycle.ps1 -BookingId VMS-SDKTEST-06
   ```

5. The read-only lookup must report exactly one GUID, `activeLifecycleState=pre-registered`, `activationType=Never`, no canonical `checkinDateUtc`, the intended future arrival, and a real departure. Review `rawEntityCheckinDateUtc` separately; it may reflect the active entity's creation time in this environment.
6. If create/update fails pre-registration read-back, stop. Inspect the record in Security Desk and via the read-only lookup before any retry. If any lifecycle operation times out or fails, inspect the current Visitor state before retrying.

## Acceptance evidence still required

- Windows build success against SDK 5.11.
- Fresh booking upsert and exact retry return one stable GUID.
- Security Desk shows the Visitor as awaiting manual check-in, with a valid appointment interval and a **Check in** action available.
- Canonical read-only lookup reports `pre-registered`; raw entity check-in property is separately displayed for diagnostics.
- Lifecycle test verifies check-in, exact retry, checkout and exact retry, with independent SDK state read-back and no credentials/access rights changed.

This is not production acceptance until those tests pass on the actual Directory.

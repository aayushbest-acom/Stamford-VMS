# Day 2 native Visitor lifecycle implementation status

## Latest target-host evidence

The operator built the bridge successfully against the installed Genetec 5.11 SDK, then ran a fresh test for `VMS-SDKTEST-07` on Directory `5.11.3130.13`.

- Create/upsert, exact retry, and field update returned the same Visitor GUID `33c34f69-44a3-46cf-b35f-e53d2d7937d3`; name, schedule, and server-report read-back passed.
- Native check-in and exact retry passed; native checkout and exact retry passed. Independent lifecycle responses reported read-back verification.
- The final read-only state showed no active Visitor, an archived `CheckedOut` visit, the original Visitor GUID and archived-visit GUID separately, both saved event IDs, and `savedCheckOutReconciledFromArchive=false`.
- The test reports no credential or access-right changes.

This verifies the native mutation/retry path on the target host, but further evidence review exposed a ledger timestamp provenance defect described below. Do not mark the entire lifecycle ledger contract accepted until the correction is built and tested on a fresh booking.

## Canonical check-in timestamp correction

On this Directory, the raw archived `Visitor.CheckinDate` can reflect entity creation time instead of the activation-based check-in time. The fresh test's archive returned `archivedCheckinDateUtc=2026-10-16T05:33:01Z`, while the check-in event was recorded around `05:53:14Z`. Source review found the normal checkout path had copied the raw archived check-in field into the checkout ledger's `GenetecCheckinDateUtc`.

The correction:

- requires the prior check-in ledger event to be read-back verified and to refer to the same Visitor GUID;
- compares its stored canonical check-in timestamp with the currently active Visitor's `Status.ActivationDate` read-back before sending checkout;
- preserves that canonical timestamp in the normal checkout event instead of overwriting it with raw archived `CheckinDate`;
- exposes `savedCheckOutMatchesArchive` and timestamp details in the read-only state result, and reports whether no action, ledger reconciliation, or manual review is warranted;
- makes the lifecycle-state script distinguish a consistent saved checkout event from a saved event whose evidence conflicts.

The patch has not yet been compiled or run on the Windows Genetec host. It must be applied and built before use. Never rerun check-in or checkout for `VMS-SDKTEST-07`; it is already archived as `CheckedOut`. After applying, inspect that booking read-only first, then validate the corrected path with a fresh unused synthetic booking such as `VMS-SDKTEST-08`.

## Known implementation boundaries

- Lifecycle sync is separately gated by startup opt-in and per-request confirmation. The C# API also accepts strictly shaped prototype-generated `VMS-UI-` + six hexadecimal IDs; the PowerShell lifecycle test harness and local-ledger reconciliation script remain restricted to `VMS-SDKTEST-*`.
- Checkout uses `Visitor.Checkout()` and requires read-only archived `CheckedOut` state and a native checkout timestamp before success is recorded.
- Checkout-ledger reconciliation is a local bridge-ledger mutation only; it does not call a Genetec lifecycle write. Reconciliation is appropriate only when archive evidence confirms checkout but the checkout event is absent. If an existing checkout event conflicts with verified timestamp evidence, stop and review rather than repeating checkout.
- The UI now persists a whole synthetic-state snapshot in local SQLite through the loopback prototype service, with localStorage fallback and optimistic revision conflict detection. It is not a production multi-user transactional domain API; server-side authentication/authorization, per-entity transactions and tamper-resistant audit remain outstanding.

## Remaining acceptance work

1. Apply the canonical timestamp refinement and build the bridge on the licensed Windows 5.11 SDK host.
2. Run a read-only state inspection for `VMS-SDKTEST-07`; do not issue lifecycle writes for it.
3. Use a fresh synthetic booking to rerun create/retry/update, check-in/retry, checkout/retry, then require the final read-only field `savedCheckOutMatchesArchive=true`.
4. Browser-accept the UI-to-bridge-to-Genetec flow including refresh/reconnection, audit/history, double-click prevention, and bridge-offline/error handling.
5. Retain redacted host build, lifecycle response, final state, and Security Center evidence.

# Day 2 target

Primary outcome: after an invited synthetic VMS booking has been created as a native Genetec Visitor, VMS check-in and check-out events should be synchronised using an SDK-supported 5.11 lifecycle operation, with a stable event id, duplicate-safe retry and independently verified read-back.

The exact Genetec native check-in/check-out API/property mapping is a design gate, not an assumption. Review the contract and test matrix in the Day 2 patch queue before enabling any lifecycle writes.


## Current Day 2 implementation note

A retry test exposed that the original Visitor upsert used cache-only `Engine.GetEntities()` for its create/update decision. See [IDEMPOTENCY-INCIDENT.md](IDEMPOTENCY-INCIDENT.md). The implementation now queries the Directory by exact stable entity name before any create/update and includes a read-only duplicate lookup endpoint. Build and live verification against the installed SDK host are still required.


## Lifecycle implementation progress

The `day2-prep` working tree contains a guarded `POST /api/genetec/visitor-lifecycle` candidate implementation, `/api/genetec/capabilities`, a durable local synthetic event ledger, explicit startup opt-in, runtime privilege checks, exact Directory booking-key lookup, and an integration script for check-in/retry/check-out/retry. The VMS UI separates its local demo state from a confirmed Genetec lifecycle state and reuses one event ID plus UTC event time on retries.

**Verified by operator:** native Visitor upsert/idempotency on `VMS-SDKTEST-02` returned the same GUID across create, exact retry and update; name, schedule, and server query read-back passed on Directory `5.11.3130.13`. The lifecycle round-trip is the next live validation gate.

**Not yet accepted:** compile and run the latest C# source against the installed Genetec 5.11 SDK host, sync one fresh `VMS-SDKTEST-*` booking, run the lifecycle script in a non-production Directory, and confirm the native check-in/check-out read-back. The script now performs a read-only exact-one-Visitor preflight before any write.

## Latest lifecycle evidence and timestamp refinement

The operator subsequently confirmed native CheckIn/retry/CheckOut/retry and independent archive read-back for `VMS-SDKTEST-07`. Review of the final timestamps showed that the archive's raw `CheckinDate` can reflect creation time, so the checkout ledger must preserve the canonical activation-based check-in timestamp from the verified CheckIn event. See [CANONICAL-CHECKIN-TIMESTAMP-PROVENANCE.md](CANONICAL-CHECKIN-TIMESTAMP-PROVENANCE.md) for the correction and safe retest. Do not rerun lifecycle writes for `VMS-SDKTEST-07`; use a fresh synthetic booking after applying/building the correction.

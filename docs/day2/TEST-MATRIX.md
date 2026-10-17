# Day 2 visitor lifecycle acceptance test matrix

Run only against the approved non-production Security Center 5.11 environment using synthetic visitors.

| ID | Scenario | Expected result |
|---|---|---|
| D2-01 | GET capability while lifecycle path is not implemented | `visitorLifecycleSyncSupported=false`; no mutation endpoint advertised as working |
| D2-02 | Startup lifecycle opt-in omitted | Capability reports disabled; lifecycle POST rejects before an SDK write |
| D2-03 | Wrong per-request confirmation | 400/403 rejection; no SDK write |
| D2-04 | Non-test or malformed booking ID | Rejected before SDK mutation |
| D2-05 | Unknown booking or ambiguous Visitor match | Fail closed; no arbitrary entity selection |
| D2-06 | Upsert invited booking with planned start in past or future | Visitor remains pre-registered: activation `Never`, no `CheckinDate`, schedule read-back verifies `VisitDate`/`Departure` |
| D2-07 | Valid CheckIn for an existing native test Visitor | Supported native operation completes; read-back confirms event and GUID |
| D2-08 | Retry same CheckIn event ID and exact body | Idempotent response, same GUID, no duplicate mutation |
| D2-09 | Same event ID with altered event type/time/booking | Conflict response; no mutation |
| D2-10 | CheckOut before a verified CheckIn | Rejected unless an explicitly approved reconciliation path is used |
| D2-11 | Valid CheckOut after verified CheckIn | Supported operation completes; read-back confirms lifecycle event and GUID |
| D2-12 | Retry same CheckOut event ID | Idempotent response, same GUID, no duplicate mutation |
| D2-13 | Disable/revoke SDK permission during operation | Error is visible; no fake `Synced` state; recovery instructions provided |
| D2-14 | Server query/read-back unavailable after SDK operation | Response is not reported as verified success; operator gets reconciliation guidance |
| D2-15 | Bridge restarts before a retry | Existing event can be reconciled; idempotency limitation documented if durable persistence is not implemented |
| D2-16 | Inspect cardholder/credential/access policy state before and after | No credential or access rights changed by the lifecycle operation |
| D2-17 | UI check-in/out event | Stable event ID; per-event Genetec sync status and audit entry track actual bridge response |
| D2-18 | Repeat native Visitor upsert for same booking ID | Directory-backed exact-name query finds the same GUID; no second Visitor is created |
| D2-19 | More than one Visitor shares a VMS booking key | Lookup reports every matching GUID; upsert returns `DUPLICATE_VMS_VISITOR_ENTITIES`; no write is attempted |
| D2-20 | Directory-backed stable-key query fails | Upsert fails closed before deciding to create a Visitor |

## Evidence to retain

- Build output from the Windows host with the exact SDK references.
- Redacted status/capability JSON before and after opt-in.
- Lifecycle test responses with secrets and real personal data removed.
- Independent Security Center view/query confirming actual state.
- Before/after confirmation that no credential or access rights changed.
- Defect/reconciliation notes for each failed or conflicting scenario.


| D2-12 | Pre-registration atomic create/update | Configure the Visitor inside one SDK transaction; fresh Directory lookup confirms `ActivationType=Never`, empty `CheckinDate`, planned `VisitDate`, and scheduled `Departure` |
| D2-13 | Partial-create recovery | If a create committed but read-back failed, same booking is blocked on retry when activation/check-in state is present; operator reconciles before a new test key is used |

| D2-14 | Archive-confirmed checkout ledger recovery | After simulated/read-back failure, read-only query confirms no active Visitor and one archived `CheckedOut` visit. Explicitly reconcile only the local ledger; verify no Genetec mutation and a stable checkout event ID on retry. |

## Canonical check-in timestamp provenance regression

**D2-21 — Checkout ledger keeps the verified native check-in timestamp:** on a fresh synthetic booking, the saved `CheckOut.GenetecCheckinDateUtc` must match the preceding read-back-verified `CheckIn.GenetecCheckinDateUtc` within two seconds, not the raw archived `Visitor.CheckinDate` diagnostic. The final read-only state must report `savedCheckOutCheckinMatchesVerifiedCheckIn=true` and `savedCheckOutMatchesArchive=true`. If either is false, do not retry checkout; inspect and repair the local ledger under operator control.

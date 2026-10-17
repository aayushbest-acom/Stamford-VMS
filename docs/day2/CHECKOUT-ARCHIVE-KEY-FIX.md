# Checkout archive lookup key correction

**Status:** Source correction prepared; target-host build and read-only state verification required.

## Evidence

- Check-in and exact check-in retry for `VMS-SDKTEST-06` passed with the same original Visitor GUID.
- The checkout request failed verification. A subsequent read-only state probe found no active Visitor, no archive through the current helper, a persisted original Visitor GUID, and the original check-in event ID with no checkout event.
- This combination is consistent with an archive lookup using the wrong identifier; it is not yet proof whether Security Center completed the checkout.

## SDK contract

The supplied Security Center 5.11 `Genetec.Sdk.xml` describes `VisitorManager.GetArchivedVisitor(Guid)` as looking up an archived visit by its archived-visit GUID. The `Genetec/DAP` visitor SDK guidance distinguishes that key from the original Visitor/Cardholder entity identifier and directs callers with the original identifier to `GetArchivedVisitorByCardholderId(originalVisitorGuid)`. See https://github-wiki-see.page/m/Genetec/DAP/wiki/platform-sdk-visitors and the installed 5.11 SDK XML members for `VisitorManager.GetArchivedVisitor` and `GetArchivedVisitorByCardholderId`.

## Change

- The shared archived lookup now calls `GetArchivedVisitorByCardholderId(originalVisitorGuid)`. This affects both post-checkout verification and the read-only lifecycle-state endpoint.
- The state response reports both `originalVisitorGuid` and `archivedVisitGuid`, plus the archive lookup key, to make the identifier distinction explicit.
- No visitor lifecycle write is sent by this patch.

## Safe next steps

1. Stop the running bridge. Apply the patch and build against the installed Genetec 5.11 SDK.
2. Start the bridge with read-only probe access; no write is needed for this verification.
3. Run `Test-NativeVisitorLifecycleState.ps1 -BookingId VMS-SDKTEST-06`.
4. If it reports `archivedVisitorFound=true` and `visitState=CheckedOut`, do not send another checkout. Reconcile the saved check-in event with the archived state under operator review.
5. If it still reports no archive, inspect the Visitor Activities / Visit Details report and Security Desk state with a Genetec administrator. Do not recreate the Visitor or repeat check-in/checkout until state is understood.

## Validation boundary

The authoring environment does not have a live Genetec runtime/Directory. The source patch can be checked statically, but the Windows SDK build and read-only query are required to confirm this hypothesis.

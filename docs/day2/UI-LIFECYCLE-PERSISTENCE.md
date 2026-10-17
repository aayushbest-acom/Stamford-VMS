# Day 2 UI reliability: local persistence and lifecycle visibility

## Current implementation

- The launcher now runs `service/vms_service.py`, which serves the static UI and a local persistence API on `127.0.0.1:8080`.
- The VMS synthetic application snapshot (visits, card requests, groups, sample analytics events, activity/audit items, QR allocation and lifecycle bridge references) is persisted to SQLite outside the repository. `STAMFORD_VMS_DATA_DIR` can override the default data directory.
- The browser hydrates from the SQLite snapshot at startup and maintains `localStorage` as a fallback convenience. Live Genetec bridge status is not restored; the operator must probe the bridge again before any action can be enabled.
- The persistence endpoint validates schema and array bounds and uses revision-based optimistic concurrency. A stale writer receives HTTP 409 and the UI stops server autosaves rather than silently replacing a newer snapshot.
- The service binds only to loopback, restricts browser origins to same-origin requests, does not enable CORS, and avoids logging query strings/request bodies.
- The service explicitly reports `prototype-only` and `authentication=not-configured`; it is not a production authentication boundary. Local processes can still reach loopback, state is client-authored, and this is a whole-snapshot store, not a transactional resource API.

## Safe acceptance checks

1. Start `Start-Demo.cmd` and verify `http://127.0.0.1:8080/api/v1/health` reports `boundToLoopback=true`, `persistence=sqlite-local`, and `authentication=not-configured`.
2. Create a synthetic demo record; verify the footer changes to `SQLITE AUTOSAVE` and that a refresh retains the snapshot.
3. Start a second browser profile before saving in either session. Save from the first, then attempt a change in the stale second session; verify it reports `SAVE CONFLICT` and does not overwrite the first snapshot.
4. Stop the service. Verify the UI labels browser fallback and does not imply that a server save succeeded.
5. Confirm that Genetec status is not cached and requires a fresh bridge probe after page load.
6. Use the UI reset action only with synthetic test state. It resets the local prototype snapshot and browser copy; it does not reset Genetec.

## Production gap

A production service still needs enterprise identity integration, server-side role enforcement, domain/resource APIs, transactional state transitions, secure per-visit QR tokens, durable idempotency/outbox and reconciliation, central immutable audit, approved retention/deletion, encryption/security controls, backups/restore and monitored operational deployment. Local SQLite snapshot persistence is a prototype improvement, not acceptance of those requirements.

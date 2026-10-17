# Day 3 — Secure QR and transactional visitor domain API

## Implemented in this prototype iteration

- Normalized SQLite `domain_visits`, `domain_events`, and `domain_audit` tables share the local database file with the existing prototype snapshot.
- `POST /api/v1/visits` creates an invited synthetic visit and returns a 32-byte random opaque token once. Only the SHA-256 digest is stored in the database.
- `GET /api/v1/visits` and `GET /api/v1/visits/{bookingId}` do not return the raw token.
- `POST /api/v1/visits/{bookingId}/qr/rotate` replaces the token for invited visits; the old token hash is replaced, so the prior pass is no longer recognised.
- `POST /api/v1/visits/{bookingId}/cancel` cancels invited visits and revokes the token.
- `POST /api/v1/qr/validate` checks shape, hash lookup, expiry, cancellation/revocation, status and whether the requested operation is currently allowed by the appointment window.
- `POST /api/v1/qr/check-in` and `/api/v1/qr/check-out` enforce valid state transitions in a SQLite `BEGIN IMMEDIATE` transaction and store UTC time, stable event ID, before/after status and audit event.
- A repeated operation with the same event ID, action and token hash returns the prior result without applying a second transition. Reusing an event ID for another action or token is rejected.
- `POST /api/v1/qr/svg` generates a real SVG QR for an active token. The token arrives in the request body and is not logged; QR SVG is not a URL containing visitor details.
- New UI visits use the domain API. Raw tokens remain in browser memory only, are stripped from snapshot/localStorage persistence, and can be rotated on a later session. Kiosk scan supports keyboard-wedge QR scanners that type their token into the scan field.
- `Start-Bridge.ps1` uses a local Windows DPAPI CurrentUser-encrypted connection profile after one-time setup. The encrypted file is outside this repository; write-scope confirmations remain separate and are not persisted.

## Local API examples

Create a synthetic invitation:

```json
{
  "id": "VMS-UI-AB12CD",
  "name": "Synthetic Visitor",
  "email": "visitor@example.test",
  "host": "Synthetic Host",
  "destination": "Main Reception",
  "visitDate": "YYYY-MM-DD",
  "window": "09:00–10:00",
  "purpose": "Automated test only"
}
```

The response contains `visit`, `qrToken`, and `qrExpiresAtUtc`. Deliver the returned token securely to the intended test user; do not save it in source, logs, exported snapshots or chat. To check in/out, send `{ "qrToken": "...", "eventId": "VMS-UI-AB12CD-CHECKIN" }` to the corresponding endpoint. Event IDs should remain unchanged for exact retries.

## Security boundary / not yet production approved

The web service remains loopback-only, but it does **not** provide enterprise authentication or role-based authorization. This is not safe for real visitor/student data or LAN exposure. The legacy browser snapshot endpoint remains client-authored and prototype-oriented. SMS, sticker printer drivers, real scanner compatibility, host identity, authenticated staff overrides, overdue scheduling/escalation, Genetec atomic synchronization/outbox, approved record ownership, backups/restore, data retention/deletion, production threat testing and formal UAT remain open. The local API's server-side audit is not yet a tamper-resistant security audit service.

Visit-window handling currently uses the host machine's local timezone: check-in is permitted from 30 minutes before planned start through 60 minutes after planned end. This implementation rule must be approved/adjusted by Stamford during policy sign-off; it is not a substitute for physical access-control enforcement in Genetec.

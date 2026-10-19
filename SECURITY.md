# Security policy and safe development rules

This repository contains a prototype connector to a physical-security system. Keep the blast radius small.

## Mandatory controls

1. Use only an approved non-production Genetec Directory and synthetic visitors for write tests.
2. Use a dedicated least-privilege SDK account. Do not use a day-to-day administrator account for repeat development where a restricted account can be provisioned.
3. Never store credentials in source, plaintext config files, screenshots, logs or patches. The bridge's optional repeat-start profile is encrypted using Windows DPAPI CurrentUser scope and stored outside the repository with a restrictive ACL; it is tied to the same Windows user/machine. The profile is not a substitute for enterprise secrets management. Use the dedicated least-privilege SDK account and rotate any secret exposed in chat/logs.
4. Keep `HttpListener` bound to `127.0.0.1`. Do not add wildcard or LAN listener prefixes without a separate security design and authentication/transport controls.
5. Keep write opt-ins and request confirmations. A health/status response may report that a write scope is enabled; this does not mean a test operation succeeded.
6. Verify every mutating SDK operation with a supported read-back or independent Security Center inspection. Do not infer success from a UI state change.
7. Do not create credentials, assign cardholder groups/escorts, alter access rights, or trigger physical access actions as part of the Day 1/Day 2 prototype without explicit approved scope.
8. If an SDK property or lifecycle API is unclear, fail closed and document the gap; do not invent a mapping.

## Secret exposure

Genetec connection credential values were shared in chat during this session. If they are live or reusable, treat them as exposed: rotate/revoke them, use a dedicated least-privilege account, and review related logs. The values themselves are deliberately not reproduced or stored in this repository.

## Reporting

Before sharing a defect report, redact usernames where unnecessary, certificate values, passwords, tokens, host/site details, and real personal data. Synthetic IDs and error codes are normally sufficient.


## Local account authentication and feature permissions (Build 5.5.0)

- The first launch exposes a one-time bootstrap form while the local access-control database has no accounts. Treat access to the Windows user profile and loopback service as privileged.
- Passwords use PBKDF2-HMAC-SHA256 (310,000 iterations, random 16-byte salt); session tokens are random and only their SHA-256 hashes are stored. Sessions expire after eight hours, login failure throttling is per username, and authenticated mutations require a CSRF token.
- API routes are mapped to feature permissions and unknown `/api/v1/` paths are denied by default. The UI visibility rules are convenience only; API enforcement is authoritative. Changes to account/group permission membership apply on the next API request.
- Only accounts in the built-in Administrator group may mutate accounts, group memberships, permission overrides or group permission sets. A last-administrator guard protects against lockout.
- This is still a local prototype. It does not provide enterprise SSO, MFA, central account provisioning, device-bound identity, externally protected audit storage or production operational hardening. Continue to use synthetic data only and keep the service loopback-only.

## Optional face-attendance feature (Build 5.2.0)

- Face attendance and group-photo identity matching are disabled by default and must not be enabled unless the school has approved the purpose, eligibility, consent/notice, retention, validation and access controls in writing. Group photo identity matching needs a separate scope approval.
- Enrol only individually consented/eligible people. The API requires consent and approval references, but because this prototype identity store is not an enterprise identity/RBAC provider those values are not proof of authority. Do not use real student images until production-grade identity/authorization controls and the approved process are implemented.
- Student samples are processed locally and encrypted with Windows DPAPI CurrentUser at rest. Group photos/camera captures are never persisted or used to create templates. Revocation deletes encrypted samples and disables matching.
- A face match is only a candidate. An operator must confirm each individual match before check-in or check-out is written. Group batches are all-or-nothing; unmatched/duplicate candidates require manual reconciliation.
- OpenCV LBPH distance is not a probability and is not a validated accuracy claim. No liveness/spoof protection is included. Do not use this prototype to make disciplinary or other consequential decisions, and do not enable group identification without a signed SRS/privacy change.


## Genetec Cardholder portrait/credential-reference import (Build 5.2.1)

- Cardholder image import is read-only and disabled by default. It requires `STAMFORD_FACE_ATTENDANCE_ENABLED=1`, `STAMFORD_BIOMETRIC_POLICY_APPROVED=1`, and `STAMFORD_GENETEC_CARDHOLDER_FACE_SYNC_APPROVED=1` in both local processes after written approval.
- The bridge runs on loopback and returns a bounded search result or an individually selected portrait. It does not persist portraits, change Cardholders or access rights, or return raw credential numbers. Credential metadata is masked to a short suffix.
- The Python service rejects unmasked credential IDs, verifies that the returned Cardholder GUID matches the selected GUID, requires consent/approval references and at least one separate individual camera sample, encrypts only face crops with Windows DPAPI CurrentUser, and binds the local enrollment/attendance event to the native Cardholder GUID.
- Attendance is not written to Genetec by this feature. It is recorded in the local VMS attendance ledger after explicit staff confirmation. A Genetec credential reference is not proof that the physical credential was presented.
- The reflection-based Cardholder picture and credential reader must be compiled and validated against the actual installed Security Center 5.11 SDK before any approved test rollout.

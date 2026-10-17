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

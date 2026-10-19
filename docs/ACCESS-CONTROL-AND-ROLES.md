# Local Accounts & Feature Permissions — Build 5.5.0

## First launch

Start `Start-Demo.cmd` and open `http://127.0.0.1:8080`. If the access-control database has no accounts, the login screen offers a one-time bootstrap form for the first Administrator. Use a unique password of at least 12 characters. Bootstrap permanently closes after that account is created. The access-control database is stored alongside the local VMS data under the current Windows user's local application-data directory. Back it up with the rest of the local databases.

## Built-in role groups

- **Administrator** — creates accounts and groups, assigns roles, adjusts each feature permission, resets passwords and reviews access-control audit events.
- **Security Operator** — visitor/security operations, group attendance, approved attendance workflows, Genetec integration operations, AI event review and operational reports.
- **Receptionist** — visitor invitations, visitor check-in/out and group/front-desk workflows; cannot administer permission groups or export reports by default.
- **Host** — create invitations and view visitor-type values; no general visitor register by default.
- **Card Counter Operator** — replacement-card queue and authorised collection actions.
- **Group Organiser** — group booking, roster and reconciliation operations.
- **Auditor** — read-only operational records, reports and available audit evidence.
- **Read-only** — view-only screens assigned by policy.

Admins can create extra permission groups, assign one or more groups to an account and add an account-specific `Allow` or `Deny` override. Deny overrides inherited grants; Allow overrides missing grants. Permission changes are read on each API request and take effect without re-login at the server. The browser refreshes displayed permissions when the user returns to the tab.

## Feature permission catalogue

The permission catalogue covers dashboard, visitor register/invitation/amend/cancel/kiosk, visitor-type catalogue, group visits, attendance, replacement cards, integration status and sending, Genetec roster/import/operations, AI event review, report preview/export, audit view, demo-snapshot view/save, account administration and permission-group administration. Unknown API routes are denied until explicitly mapped.

## Security controls included

- Passwords use PBKDF2-HMAC-SHA256 with 310,000 iterations and random salt. Plaintext passwords are never stored.
- Random session tokens are represented in SQLite by SHA-256 hashes; session lifetime is eight hours. Disabling an account or resetting its password revokes sessions.
- Failed logins are throttled per username, and successful/failed logins, account/group changes and permission overrides are audited.
- Authenticated state-changing requests require a CSRF token; same-origin/loopback Host checks remain in place.
- All `/api/v1/` routes require an explicit permission mapping except documented authentication and health routes. The API is authoritative; hiding a screen is not the security boundary.
- Only members of the built-in Administrator group can view or mutate account/permission administration. Delegated `accounts.manage` or `permissions.manage` flags alone do not allow self-promotion. The last-administrator guard requires active built-in Administrator membership and both administrative capabilities.
- Authenticated demo snapshot data is not written to browser local storage. The existing local service continues to provide server-side persistence.

## Limitations / deployment gate

This is local prototype authentication, not production IAM. It does not provide SSO, MFA, school identity lifecycle integration, centrally managed account recovery, tamper-resistant remote audit storage, enterprise secrets management or a formal security certification. The local HTTP service must remain bound to loopback. Use synthetic data only until Stamford approves the identity design, roles, access matrix and production hardening.


### Sensitive Genetec enrolment

The `POST /api/v1/face-attendance/enroll-from-genetec` operation requires both `attendance.manage` and `genetec.manage`; permission to manage attendance alone does not authorise fetching/importing Genetec identity imagery.

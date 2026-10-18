# Visitor Types Catalogue — Build 5.3.1

## Behaviour

The visitor invitation form includes a required **Visitor type** dropdown. The options come from the SQLite-backed `domain_visitor_types` catalogue, not a fixed HTML enum. The service verifies the supplied `visitorTypeId` against the currently active catalogue before it creates an invitation.

The catalogue is available from **Workspace → Visitor types**. The local prototype supports adding custom types, renaming a type, deactivating a type, and reactivating it. Types are never hard-deleted, because existing invitations retain the type name that was saved when the invitation was created.

## Pre-populated types

- Parent / Guardian
- Prospective Family
- Contractor
- Vendor / Supplier
- Delivery / Courier
- Service Provider
- Event Guest
- Volunteer
- Alumni
- Staff / Employee
- Student
- General Visitor
- Other

At least one type must remain active. New invitations may only use active types. If an unknown or deactivated ID is submitted directly to the API, the server rejects the invitation rather than accepting an arbitrary free-text category.

## API

- `GET /api/v1/visitor-types?includeInactive=1` — return the catalogue, including inactive rows.
- `GET /api/v1/visitor-types?includeInactive=0` — return active options only.
- `POST /api/v1/visitor-types` with `{ "name": "Exam Candidate", "description": "Optional description" }` — create an active custom category.
- `POST /api/v1/visitor-types/{typeId}/update` — update `name`, `description`, and/or boolean `isActive`.
- `POST /api/v1/visits` — include `visitorTypeId` with the normal invitation fields. When omitted by older callers, the server uses `general-visitor` for compatibility.

Visitor type names are normalized, limited to 2–64 characters, and unique without regard to case for ordinary ASCII names. Descriptions are limited to 160 characters. Type changes are audited in the local domain audit table.

## SQLite migration and history

The service creates `domain_visitor_types` and seeds missing defaults on startup. Existing `domain_visits` tables gain `visitor_type_id` and `visitor_type_name` columns through an additive migration. Existing visits are assigned `general-visitor` / `General Visitor`. Their booking IDs, QR token digests, visit states and timestamps are not rewritten. Each new invitation also stores a snapshot of the type's display name, so renaming a catalogue item later does not silently relabel historical visits.

## Security / prototype boundary

The service remains loopback-only and has no enterprise authentication or RBAC. The type management screen is therefore a prototype administrator experience, not a production authorization boundary. Keep synthetic data only until application authentication, permission enforcement, and operational change approval are implemented.

## Verification

Run:

```powershell
python -m py_compile service\domain.py service\vms_service.py tests\test_domain_api.py tests\test_repository_contracts.py
python -m unittest discover -s tests -v
```

The tests cover default types, custom type creation, case-insensitive duplicate rejection, inactive type enforcement, visit type snapshots, legacy SQLite migration, and the UI/API contract.

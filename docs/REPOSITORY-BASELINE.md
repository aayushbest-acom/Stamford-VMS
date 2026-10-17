# Repository baseline

## Base source

This repository is based on the `StamfordVMS-Day1-Final.zip` project package dated 15 October 2026, which contains the Stamford VMS browser prototype, local Genetec 5.11 bridge starter, synthetic QR images, bridge scripts, and field mapping notes.

## Source projects used as references

- `ACOM-Stamford-School-Genetec-Sync-main.zip`: reference for the existing `Engine.LoginManager.LogOn` and `Engine.CreateEntity` patterns. Its data sync workflow is not copied as an active VMS feature.
- `Genetec.ReadOnlyPoc(4).zip`: reference for the SDK connection, Directory version and configuration query patterns. The original diagnostic program's full behavior is not run by this bridge.

The two original archives are intentionally not included: they are large and may contain site configuration or secrets. Keep them in restricted storage and do not commit them wholesale.

## Day 1 reported verification

Operator-provided output confirms:

- `connected: true`
- SDK `5.11.0.0`
- Directory `5.11.3130.13`
- Cardholder-group query `success`, count `11`
- Visitor Management licence `true`
- Visitor sync opt-in reported `true`
- Frontend server started at `127.0.0.1:8080`

The operator had enabled both write scopes at startup. The supplied output does not show the native Visitor upsert test result, so it is not evidence that create/update/read-back has passed.

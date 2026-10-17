# Development workflow

## Branching and patches

- Treat the current repository state as the Day 1 baseline.
- Apply the prepared Day 2 series in order, or create a branch from the Day 1 baseline and use `git am`.
- Make each patch narrow and reviewable. Separate SDK changes, UI changes and documentation/test updates where possible.
- Do not squash away integration evidence needed to trace a Genetec operation.

## Before committing

- Run `dotnet build .\GenetecReadOnlyBridge.csproj /p:Configuration=Debug` on the SDK workstation.
- Run `Test-BridgeReadOnly.ps1`; record its actual redacted output.
- For write-enabled changes, run the specific guarded test script only in the approved non-production Directory, inspect the resulting entity in Security Center, and save redacted evidence.
- Syntax-check the frontend and any PowerShell scripts.
- Confirm no secrets were added: `git diff --check` and inspect `git diff` before committing.

## Evidence labels

Use distinct labels: **Implemented**, **Static-checked**, **Built on SDK host**, **Tested against Genetec**, and **Accepted by Stamford**. Do not report these states as equivalent.

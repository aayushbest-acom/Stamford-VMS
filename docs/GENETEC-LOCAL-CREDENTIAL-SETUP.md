# One-time Genetec connection setup (Windows)

The bridge launcher can remember the Genetec connection fields without placing secrets in this repository. The profile uses Windows DPAPI `CurrentUser` encryption and a restrictive ACL under `%LOCALAPPDATA%\StamfordVMS\Secrets\genetec-connection.dpapi`.

## Before setup

A reusable credential value was shared in chat. Treat it as exposed: rotate it and use a dedicated least-privilege Genetec SDK account where possible. Do not use a daily administrator account for repeat development if a restricted account can be created. Do not paste credentials into source files, `.env` files, issue trackers or repository configuration.

## Save once

Run this on the **Windows workstation where the bridge will run**, while signed in as the Windows user that will launch the bridge:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\GenetecReadOnlyBridge\Save-GenetecCredentials.ps1
```

Enter the server, username, certificate value and password at the one-time prompts. Certificate and password inputs are hidden. If the SDK is not in the default Security Center 5.11 SDK folder, provide the correct folder containing `Genetec.Sdk.dll`.

The bridge launcher intentionally does not provision credentials automatically. This keeps startup non-interactive and avoids prompting for or accidentally exposing secrets in a launcher session. Complete this one-time setup first if the profile does not exist.

## Every later launch

Run `GenetecReadOnlyBridge\Start-Bridge.ps1` as the same Windows user on the same workstation. The connection fields are loaded from the DPAPI profile automatically. If credentials or the SDK installation changes, rerun `Save-GenetecCredentials.ps1` to replace the profile.

The DPAPI file is machine/user-bound: do not copy it to another machine or account, and do not share it. The connection fields are supplied to the bridge process environment only while it runs and are cleared on exit. Normal startup has no prompts and defaults to read-only mode. For an approved non-production environment, use `GenetecReadOnlyBridge\Start-Bridge-VisitorSync.cmd` to enable the guarded invited-Visitor create/update scope without prompts. Other test scopes still require explicit command-line switches. Each write endpoint continues to enforce request-level and synthetic-booking guardrails. Missing prerequisites fail fast with an error message rather than an interactive prompt.

## Validation limitation

This environment cannot execute Windows DPAPI, ACL changes, PowerShell, or the Genetec SDK. Confirm profile creation and automatic reload on the target host before relying on this convenience.

param(
    [switch]$EnableTestCardholderWrites,
    [switch]$EnableVisitorSync,
    [switch]$EnableVisitorLifecycleSync
)

$ErrorActionPreference = 'Stop'

# This launcher is deliberately non-interactive. Mutating scopes are disabled by
# default and can only be enabled explicitly as script switches. Request-level
# confirmation, synthetic-ID restrictions, licence/version checks and SDK
# privilege checks remain enforced by the bridge API.
. (Join-Path $PSScriptRoot 'Genetec-CredentialStore.ps1')
Write-Host 'Stamford VMS - Genetec 5.11 bridge' -ForegroundColor Cyan
Write-Host 'Non-interactive startup; connection profile is read from Windows DPAPI CurrentUser storage.' -ForegroundColor Gray

$genetecProfile = Get-StamfordGenetecCredentialProfile
if (-not $genetecProfile) {
    $credentialPath = Get-StamfordGenetecCredentialPath
    throw "No saved Genetec profile was found at '$credentialPath'. Run Save-GenetecCredentials.ps1 once in this Windows account, then launch the bridge again. Startup does not prompt for secrets."
}

$defaultSdk = 'C:\Program Files (x86)\Genetec Security Center 5.11 SDK'
if (-not [string]::IsNullOrWhiteSpace($env:GSC_SDK)) {
    $sdk = $env:GSC_SDK
} elseif ($genetecProfile.sdkDirectory -and (Test-Path (Join-Path ([string]$genetecProfile.sdkDirectory) 'Genetec.Sdk.dll'))) {
    $sdk = [string]$genetecProfile.sdkDirectory
} else {
    $sdk = $defaultSdk
}
if (-not (Test-Path (Join-Path $sdk 'Genetec.Sdk.dll'))) {
    throw "Genetec.Sdk.dll was not found. Checked '$sdk'. Install the compatible Genetec Security Center 5.11 SDK, or set GSC_SDK to its directory before launching. Startup does not prompt for a path."
}
$env:GSC_SDK = $sdk

$exe = Join-Path $PSScriptRoot 'bin\Debug\GenetecReadOnlyBridge.exe'
if (-not (Test-Path $exe)) {
    throw "Bridge executable not found at '$exe'. Build GenetecReadOnlyBridge.csproj on this Windows host with the compatible Genetec SDK installed, then start the bridge again."
}

# Supply the saved connection fields only to this child bridge process. Do not
# write secrets to source, command-line arguments, logs or repository settings.
$env:GENETEC_SERVER = [string]$genetecProfile.server
$env:GENETEC_USERNAME = [string]$genetecProfile.username
$env:GENETEC_CERT = [string]$genetecProfile.certificate
$env:GENETEC_PASSWORD = [string]$genetecProfile.password
$env:GENETEC_PORT = [string]$(if ($genetecProfile.port) { $genetecProfile.port } else { 5500 })
$env:GENETEC_ENABLE_TEST_WRITES = if ($EnableTestCardholderWrites) { '1' } else { '0' }
$env:GENETEC_ENABLE_VISITOR_SYNC = if ($EnableVisitorSync) { '1' } else { '0' }
$env:GENETEC_ENABLE_VISITOR_LIFECYCLE_SYNC = if ($EnableVisitorLifecycleSync) { '1' } else { '0' }
$genetecProfile = $null

try {
    $writeMode = if ($EnableTestCardholderWrites -or $EnableVisitorSync -or $EnableVisitorLifecycleSync) { 'one or more explicitly requested, guarded test write scopes' } else { 'read-only (default)' }
    Write-Host "SDK: $sdk" -ForegroundColor Gray
    Write-Host "Write mode: $writeMode" -ForegroundColor $(if ($writeMode -eq 'read-only (default)') { 'Green' } else { 'Yellow' })
    Write-Host 'Listener: 127.0.0.1:5190' -ForegroundColor Green
    & $exe
    if ($LASTEXITCODE -ne 0) { throw "Genetec bridge exited with code $LASTEXITCODE." }
} finally {
    foreach ($name in @('GENETEC_PASSWORD','GENETEC_SERVER','GENETEC_USERNAME','GENETEC_CERT','GENETEC_PORT','GENETEC_ENABLE_TEST_WRITES','GENETEC_ENABLE_VISITOR_SYNC','GENETEC_ENABLE_VISITOR_LIFECYCLE_SYNC','GSC_SDK')) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    $genetecProfile = $null
    Remove-Variable password, cert, username, server, ptr, securePassword -ErrorAction SilentlyContinue
}

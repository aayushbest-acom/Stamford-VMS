$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Genetec-CredentialStore.ps1')

$sdk = $env:GSC_SDK
if ([string]::IsNullOrWhiteSpace($sdk)) { $sdk = 'C:\Program Files (x86)\Genetec Security Center 5.11 SDK' }
if (-not (Test-Path (Join-Path $sdk 'Genetec.Sdk.dll'))) {
    $sdk = Read-Host "SDK directory containing Genetec.Sdk.dll [$sdk]"
    if ([string]::IsNullOrWhiteSpace($sdk)) { $sdk = 'C:\Program Files (x86)\Genetec Security Center 5.11 SDK' }
}
if (-not (Test-Path (Join-Path $sdk 'Genetec.Sdk.dll'))) { throw "Genetec.Sdk.dll not found under $sdk. Set GSC_SDK or provide the approved SDK directory." }

$newProfile = Read-StamfordGenetecCredentialProfileInteractive -SdkDirectory $sdk
$path = Save-StamfordGenetecCredentialProfile $newProfile
$newProfile = $null
Write-Host "Credentials saved in an encrypted per-user Windows DPAPI profile: $path" -ForegroundColor Green
Write-Host 'The file is tied to this Windows user on this machine; it contains no plaintext credential fields.' -ForegroundColor Gray
Write-Host 'Next, run Start-Bridge.cmd (or Start-Bridge.ps1). Startup is non-interactive and read-only by default; optional test write scopes are selected with explicit launcher switches.' -ForegroundColor Cyan

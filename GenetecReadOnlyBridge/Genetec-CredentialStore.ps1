# Local-only Genetec connection profile encrypted with Windows DPAPI CurrentUser.
# The plaintext profile is never written to disk or included in this repository.
$script:StamfordGenetecEntropy = [System.Text.Encoding]::UTF8.GetBytes('StamfordVMS.Geneteclocal.Profile.v1')

function Get-StamfordGenetecCredentialPath {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'LOCALAPPDATA is not available. Run this script in a normal interactive Windows user session.'
    }
    return (Join-Path $env:LOCALAPPDATA 'StamfordVMS\Secrets\genetec-connection.dpapi')
}

function Set-StamfordCredentialFileAcl([string]$Path, [switch]$Directory) {
    $icacls = Join-Path $env:SystemRoot 'System32\icacls.exe'
    if (-not (Test-Path $icacls)) { throw 'Windows icacls.exe was not found; cannot set a restricted credential-store ACL.' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    if ($Directory) {
        & $icacls $Path '/inheritance:r' '/grant:r' "$($identity):(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
    } else {
        & $icacls $Path '/inheritance:r' '/grant:r' "$($identity):F" 'SYSTEM:F' | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict credential-store permissions. The credential profile was not accepted as saved.' }
}

function Save-StamfordGenetecCredentialProfile([object]$Profile) {
    Add-Type -AssemblyName System.Security
    $path = Get-StamfordGenetecCredentialPath
    $directory = Split-Path -Parent $path
    if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    Set-StamfordCredentialFileAcl -Path $directory -Directory
    $json = ConvertTo-Json -InputObject $Profile -Depth 4 -Compress
    $plain = [System.Text.Encoding]::UTF8.GetBytes($json)
    try {
        $cipher = [Security.Cryptography.ProtectedData]::Protect($plain, $script:StamfordGenetecEntropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        [System.IO.File]::WriteAllText($path, [Convert]::ToBase64String($cipher), [System.Text.Encoding]::ASCII)
        Set-StamfordCredentialFileAcl -Path $path
    } finally {
        [Array]::Clear($plain, 0, $plain.Length)
        if ($cipher) { [Array]::Clear($cipher, 0, $cipher.Length) }
    }
    return $path
}

function Get-StamfordGenetecCredentialProfile {
    $path = Get-StamfordGenetecCredentialPath
    if (-not (Test-Path $path)) { return $null }
    Add-Type -AssemblyName System.Security
    $cipher = $null; $plain = $null
    try {
        $cipher = [Convert]::FromBase64String([System.IO.File]::ReadAllText($path, [System.Text.Encoding]::ASCII).Trim())
        $plain = [Security.Cryptography.ProtectedData]::Unprotect($cipher, $script:StamfordGenetecEntropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $genetecProfileObject = [System.Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json
        foreach ($key in @('server','username','certificate','password')) {
            if ([string]::IsNullOrWhiteSpace([string]$genetecProfileObject.$key)) { throw "Required field '$key' is missing from the encrypted profile." }
        }
        return $genetecProfileObject
    } catch {
        throw 'The encrypted Genetec profile could not be opened by this Windows user. Run Save-GenetecCredentials.ps1 in the original account to replace it. No credentials were logged.'
    } finally {
        if ($plain) { [Array]::Clear($plain, 0, $plain.Length) }
        if ($cipher) { [Array]::Clear($cipher, 0, $cipher.Length) }
    }
}

function ConvertFrom-StamfordSecureString([Security.SecureString]$Value) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Read-StamfordGenetecCredentialProfileInteractive([string]$SdkDirectory) {
    Write-Host 'One-time setup: connection details will be encrypted with Windows DPAPI for the current Windows user.' -ForegroundColor Cyan
    $server = Read-Host 'Genetec Directory host (for example localhost)'
    $username = Read-Host 'Genetec username (prefer a dedicated least-privilege SDK account)'
    $certificateSecure = Read-Host 'Genetec certificate value' -AsSecureString
    $passwordSecure = Read-Host 'Genetec password' -AsSecureString
    try {
        $certificate = ConvertFrom-StamfordSecureString $certificateSecure
        $password = ConvertFrom-StamfordSecureString $passwordSecure
        if ([string]::IsNullOrWhiteSpace($server) -or [string]::IsNullOrWhiteSpace($username) -or [string]::IsNullOrWhiteSpace($certificate) -or [string]::IsNullOrWhiteSpace($password)) {
            throw 'Server, username, certificate and password must all be supplied.'
        }
        return [pscustomobject]@{ server=$server.Trim(); username=$username.Trim(); certificate=$certificate; password=$password; port=5500; sdkDirectory=$SdkDirectory }
    } finally {
        $certificate = $null; $password = $null
        if ($certificateSecure) { $certificateSecure.Dispose() }
        if ($passwordSecure) { $passwordSecure.Dispose() }
    }
}

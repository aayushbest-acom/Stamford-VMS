$ErrorActionPreference = 'Stop'
$baseUrl = 'http://127.0.0.1:5190'
$expectedBridgeBuild = '5.2.4'
$bridgeDir = $PSScriptRoot
$startScript = Join-Path $bridgeDir 'Start-Bridge.ps1'
$exe = Join-Path $bridgeDir 'bin\Debug\GenetecReadOnlyBridge.exe'
$credentialPath = Join-Path $env:LOCALAPPDATA 'StamfordVMS\Secrets\genetec-connection.dpapi'

function Test-BridgeListener {
    try {
        $null = Invoke-RestMethod -Uri "$baseUrl/api/health" -Method Get -TimeoutSec 2 -ErrorAction Stop
        return $true
    } catch { return $false }
}
function Get-BridgeHealth {
    try { return Invoke-RestMethod -Uri "$baseUrl/api/health" -Method Get -TimeoutSec 2 -ErrorAction Stop }
    catch { return $null }
}
function Get-GenetecStatus {
    try { return Invoke-RestMethod -Uri "$baseUrl/api/genetec/status" -Method Get -TimeoutSec 3 -ErrorAction Stop }
    catch { return $null }
}
function Stop-StalePrototypeBridge($health) {
    # HttpListener may be hosted by HTTP.sys, so the TCP listener PID is not
    # reliably the user process. Identify the process by the executable path
    # reported by the running bridge, or fall back only when one uniquely named
    # Stamford bridge process exists. Never kill an unrelated process.
    $runningPath = if ($health) { [string]$health.bridgeExecutablePath } else { '' }
    $candidates = @()
    try {
        $allBridgeProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'GenetecReadOnlyBridge.exe'" -ErrorAction Stop)
        if (-not [string]::IsNullOrWhiteSpace($runningPath)) {
            $candidates = @($allBridgeProcesses | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ieq $runningPath })
        } elseif ($allBridgeProcesses.Count -eq 1) {
            $candidates = $allBridgeProcesses
        }
    } catch {
        Write-Warning 'A stale bridge is listening but its executable could not be identified safely. Stop the old StamfordVMS GenetecReadOnlyBridge.exe manually; no process was killed.'
        return $false
    }
    if ($candidates.Count -ne 1) {
        Write-Warning 'A stale bridge is listening but the matching Stamford bridge process is not unique. Stop the older bridge manually; no process was killed.'
        return $false
    }
    try { Stop-Process -Id ([int]$candidates[0].ProcessId) -Force -ErrorAction Stop }
    catch {
        Write-Warning 'Could not stop the stale Stamford VMS bridge process. Stop it manually and rerun the launcher.'
        return $false
    }
    for ($wait = 0; $wait -lt 10; $wait++) {
        Start-Sleep -Milliseconds 300
        if (-not (Test-BridgeListener)) { return $true }
    }
    Write-Warning 'The older bridge did not release port 5190. Stop it manually and rerun the launcher.'
    return $false
}

$forceBuild = $false
# Reuse only the expected bridge build. Older/unversioned listeners are stopped
# only when the process owning 5190 is exactly GenetecReadOnlyBridge.exe.
if (Test-BridgeListener) {
    $health = Get-BridgeHealth
    if ($health -and [string]$health.bridgeBuild -eq $expectedBridgeBuild) {
        $status = Get-GenetecStatus
        if ($status -and $status.connected -eq $true) {
            Write-Host ("Genetec already connected at {0} (bridge build {1}, Directory {2}); reusing current bridge." -f $baseUrl, $health.bridgeBuild, $status.directoryVersion)
        } else {
            Write-Warning 'The current bridge build is correct, but its Genetec session is not verified. Not starting a duplicate; inspect bridge logs if it remains disconnected.'
        }
        exit 0
    }
    Write-Warning ("A stale or unversioned bridge is responding on 5190 (reported build: {0}; expected: {1}). Restarting the Stamford VMS bridge so the current fix is loaded." -f $(if ($health) { $health.bridgeBuild } else { 'unavailable' }), $expectedBridgeBuild)
    if (-not (Stop-StalePrototypeBridge $health)) { exit 0 }
    $forceBuild = $true
}

# Missing local prerequisites must not prevent the VMS UI from opening.
if (-not (Test-Path $credentialPath)) {
    Write-Warning 'Automatic Genetec connection skipped: no saved Windows DPAPI profile exists for this Windows account. VMS will still start.'
    exit 0
}
if (-not (Test-Path $startScript)) {
    Write-Warning 'Automatic Genetec connection skipped: the bridge startup script is missing. VMS will still start.'
    exit 0
}

$localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
$logDir = Join-Path $localAppData 'StamfordVMS\Logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = Join-Path $logDir "genetec-bridge-$stamp.log"
$stderr = Join-Path $logDir "genetec-bridge-$stamp-error.log"

# A clean repository archive intentionally excludes build products. Build the
# bridge on this Windows host on first launch or after stopping a stale version, using the SDK path from the saved
# profile (or the documented local default), without prompting for any values.
if ($forceBuild -or -not (Test-Path $exe)) {
    $sdk = $env:GSC_SDK
    if ([string]::IsNullOrWhiteSpace($sdk)) {
        try {
            . (Join-Path $bridgeDir 'Genetec-CredentialStore.ps1')
            $savedProfile = Get-StamfordGenetecCredentialProfile
            if ($savedProfile -and -not [string]::IsNullOrWhiteSpace([string]$savedProfile.sdkDirectory)) {
                $sdk = [string]$savedProfile.sdkDirectory
            }
            $savedProfile = $null
            Remove-Variable savedProfile -ErrorAction SilentlyContinue
        } catch {
            Write-Warning 'The saved local Genetec profile could not be read for SDK discovery. No secrets were logged; VMS will still start.'
            exit 0
        }
    }
    if ([string]::IsNullOrWhiteSpace($sdk)) { $sdk = 'C:\Program Files (x86)\Genetec Security Center 5.11 SDK' }
    if (-not (Test-Path (Join-Path $sdk 'Genetec.Sdk.dll'))) {
        Write-Warning ("Automatic Genetec build skipped: Genetec.Sdk.dll was not found under '{0}'. VMS will still start." -f $sdk)
        exit 0
    }
    $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
    if (-not $dotnetCommand) {
        Write-Warning 'Automatic Genetec build skipped: dotnet was not found in PATH. VMS will still start.'
        exit 0
    }
    $project = Join-Path $bridgeDir 'GenetecReadOnlyBridge.csproj'
    $buildLog = Join-Path $logDir "genetec-bridge-build-$stamp.log"
    try {
        $buildOutput = & $dotnetCommand.Source build $project --configuration Debug "-p:GSC_SDK=$sdk" 2>&1
        $buildExitCode = $LASTEXITCODE
        $buildOutput | Out-File -FilePath $buildLog -Encoding utf8
        if ($buildExitCode -ne 0 -or -not (Test-Path $exe)) {
            Write-Warning ("Automatic Genetec bridge build failed (exit {0}). See {1}. VMS will still start." -f $buildExitCode, $buildLog)
            exit 0
        }
        Write-Host ("Genetec bridge built automatically; build log: {0}" -f $buildLog)
    } catch {
        Write-Warning ("Automatic Genetec bridge build failed. See {0}; VMS will still start." -f $buildLog)
        exit 0
    }
}

$powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{0}" -EnableVisitorSync -EnableVisitorLifecycleSync' -f $startScript

try {
    $proc = Start-Process -FilePath $powershellExe -ArgumentList $arguments -WorkingDirectory $bridgeDir -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
} catch {
    Write-Warning 'Automatic Genetec connection could not start the local bridge. VMS will still start; inspect the startup logs and run the bridge launcher manually if needed.'
    exit 0
}

# Give the SDK session a short window to establish; don't block indefinitely.
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    if (Test-BridgeListener) {
        $status = Get-GenetecStatus
        if ($status -and $status.connected -eq $true) {
            Write-Host ("Genetec connected automatically (Directory {0}). Bridge runs in the background. Logs: {1}" -f $status.directoryVersion, $stdout)
            exit 0
        }
    }
    if ($proc.HasExited) { break }
}
Write-Warning ("The VMS will start, but Genetec connection was not verified within 15 seconds. Check logs: {0} and {1}" -f $stdout, $stderr)
exit 0

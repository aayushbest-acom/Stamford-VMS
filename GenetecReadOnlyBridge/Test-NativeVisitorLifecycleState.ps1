[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:5190',
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^VMS-SDKTEST-[A-Z0-9-]{1,16}$')]
    [string]$BookingId
)

$ErrorActionPreference = 'Stop'
Write-Host 'Read-only native Visitor lifecycle inspection' -ForegroundColor Cyan
Write-Host "Bridge: $BaseUrl"
Write-Host "Synthetic booking: $BookingId"
Write-Host 'This calls a GET endpoint only. No lifecycle or Visitor writes will be sent.' -ForegroundColor Yellow

$uri = "$BaseUrl/api/genetec/visitor-lifecycle-state?bookingId=$([Uri]::EscapeDataString($BookingId))"
try {
    $state = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 30
}
catch {
    $responseBody = $null
    try {
        if ($_.Exception.Response -and $_.Exception.Response.GetResponseStream()) {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            try { $responseBody = $reader.ReadToEnd() } finally { $reader.Dispose() }
        }
    } catch { }
    if ($responseBody) { Write-Host $responseBody }
    throw "Read-only lifecycle inspection failed. No writes were sent. $($_.Exception.Message)"
}

$state | ConvertTo-Json -Depth 8 | Write-Host
if ($state.writeAttempted -eq $true) { throw 'Safety invariant violated: lifecycle-state endpoint must never attempt writes.' }
if ($state.duplicateDetected -eq $true) {
    Write-Host 'STOP: duplicate Visitors detected. Reconcile before any lifecycle write.' -ForegroundColor Red
    exit 2
}
if ($state.archivedVisitorFound -eq $true -and $state.visitState -eq 'CheckedOut') {
    if ([string]::IsNullOrWhiteSpace([string]$state.savedCheckOutEventId) -and -not [string]::IsNullOrWhiteSpace([string]$state.savedCheckInEventId)) {
        Write-Host 'READ-ONLY RESULT: Visitor is already archived as CheckedOut and the ledger has no checkout event.' -ForegroundColor Yellow
        Write-Host ("After confirming this archive state, run .\Reconcile-NativeVisitorCheckout.ps1 -BookingId {0} to reconcile the LOCAL ledger only. It will not issue a Genetec write." -f $BookingId) -ForegroundColor Yellow
    }
    elseif (-not [string]::IsNullOrWhiteSpace([string]$state.savedCheckOutEventId) -and $state.savedCheckOutMatchesArchive -eq $true) {
        Write-Host 'PASS: archived CheckedOut state, saved checkout event, archived checkout timestamp, and canonical check-in timestamp all agree.' -ForegroundColor Green
        Write-Host 'No further lifecycle mutation or ledger reconciliation is needed for this booking.' -ForegroundColor Green
    }
    elseif (-not [string]::IsNullOrWhiteSpace([string]$state.savedCheckOutEventId)) {
        Write-Host 'STOP: a checkout ledger event exists, but its read-back evidence does not fully match the archive and verified check-in timestamp.' -ForegroundColor Red
        Write-Host 'Do not retry checkout. Review the ledger/archive details and repair the local ledger under operator control.' -ForegroundColor Red
        exit 4
    }
    else {
        Write-Host 'STOP: archive state is CheckedOut but a verified check-in event is missing. Do not retry checkout; administrator review is required.' -ForegroundColor Red
        exit 4
    }
    exit 0
}
if ($state.activeVisitorFound -eq $true) {
    Write-Host "READ-ONLY RESULT: activeLifecycleState=$($state.activeLifecycleState). Inspect the operation stage before any retry." -ForegroundColor Yellow
    exit 0
}
Write-Host 'READ-ONLY RESULT: no active visitor and no confirmed archived record. Do not retry writes until an administrator reconciles.' -ForegroundColor Yellow

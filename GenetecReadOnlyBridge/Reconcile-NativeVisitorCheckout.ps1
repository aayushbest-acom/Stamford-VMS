[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:5190',
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^VMS-SDKTEST-[A-Z0-9-]{1,16}$')]
    [string]$BookingId
)

$ErrorActionPreference = 'Stop'
$confirmation = 'RECONCILE VERIFIED ARCHIVED CHECKOUT TO LOCAL LEDGER'
$escapedBookingId = [Uri]::EscapeDataString($BookingId)
$stateUri = "$BaseUrl/api/genetec/visitor-lifecycle-state?bookingId=$escapedBookingId"

Write-Host 'Reconcile an already-verified archived checkout into the local event ledger' -ForegroundColor Cyan
Write-Host "Bridge: $BaseUrl"
Write-Host "Synthetic booking: $BookingId"
Write-Host 'This operation writes ONLY the local bridge ledger. It must not send any Genetec lifecycle mutation.' -ForegroundColor Yellow

try {
    $before = Invoke-RestMethod -Method Get -Uri $stateUri -TimeoutSec 30
}
catch {
    throw "Read-only preflight failed. No ledger or Genetec writes were attempted. $($_.Exception.Message)"
}
$before | ConvertTo-Json -Depth 8 | Write-Host

if ($before.success -ne $true -or $before.writeAttempted -eq $true) {
    throw 'The state endpoint did not return a successful read-only result. Stop; no reconciliation was attempted.'
}
if ($before.duplicateDetected -eq $true) {
    throw 'Duplicate Visitor matches were detected. Have an authorised administrator reconcile them first.'
}
if ($before.activeVisitorFound -eq $true -or [int]$before.activeMatchCount -gt 0) {
    throw 'An active Visitor still exists. This reconciliation is only for a confirmed archived checkout.'
}
if ($before.archivedVisitorFound -ne $true -or $before.visitState -ne 'CheckedOut') {
    throw 'Genetec does not currently confirm an archived CheckedOut visit. No reconciliation was attempted.'
}
if ([string]::IsNullOrWhiteSpace([string]$before.visitorGuid) -or
    [string]::IsNullOrWhiteSpace([string]$before.archivedCheckoutDateUtc)) {
    throw 'The archived Visitor GUID or checkout timestamp is missing. No reconciliation was attempted.'
}
if ([string]::IsNullOrWhiteSpace([string]$before.savedCheckInEventId)) {
    throw 'The local ledger has no saved check-in event. Manual review is required before reconciliation.'
}
if (-not [string]::IsNullOrWhiteSpace([string]$before.savedCheckOutEventId)) {
    if ($before.savedCheckOutMatchesArchive -eq $true) {
        Write-Host "PASS: checkout event '$($before.savedCheckOutEventId)' already agrees with archived checkout and the verified check-in timestamp. No change is needed." -ForegroundColor Green
        exit 0
    }
    throw "A checkout ledger event already exists ('$($before.savedCheckOutEventId)'), but its timestamp evidence does not agree with the archived state. Do not run reconciliation or any checkout again; inspect and repair the local ledger under operator control."
}

Write-Host 'Preflight passed: active Visitor count is zero, Genetec archive state is CheckedOut, a saved verified check-in event exists, and no checkout event is in the ledger.' -ForegroundColor Green
Write-Host "Visitor GUID: $($before.visitorGuid)"
Write-Host "Archived visit GUID: $($before.archivedVisitGuid)"
Write-Host "Native checkout timestamp (UTC): $($before.archivedCheckoutDateUtc)"
Write-Host 'This action creates a reconciliation entry from confirmed Genetec archive evidence. It will NOT call Visitor.Checkout() or any other Genetec write.' -ForegroundColor Yellow
$typed = Read-Host 'Type RECONCILE VERIFIED ARCHIVED CHECKOUT TO LOCAL LEDGER to continue'
if ($typed -cne $confirmation) {
    Write-Host 'Cancelled. No reconciliation was attempted.' -ForegroundColor Yellow
    exit 3
}

$body = @{
    Confirmation = $confirmation
    BookingId = $BookingId
} | ConvertTo-Json -Depth 4

try {
    $result = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/genetec/visitor-lifecycle-reconcile-checkout" -ContentType 'application/json' -Body $body -TimeoutSec 45
}
catch {
    $responseBody = $null
    try {
        if ($_.Exception.Response -and $_.Exception.Response.GetResponseStream()) {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            try { $responseBody = $reader.ReadToEnd() } finally { $reader.Dispose() }
        }
    } catch { }
    if ($responseBody) { Write-Host $responseBody -ForegroundColor Red }
    throw "Ledger reconciliation failed. Do not retry automatically; run the read-only state inspection and review bridge logs. $($_.Exception.Message)"
}

$result | ConvertTo-Json -Depth 8 | Write-Host
if ($result.success -ne $true -or $result.genetecWriteAttempted -ne $false -or $result.readBackVerified -ne $true) {
    throw 'The endpoint did not confirm a successful ledger-only reconciliation. Run read-only state inspection before taking any further action.'
}

Write-Host 'Verifying the result using the read-only lifecycle state endpoint...' -ForegroundColor Cyan
$after = Invoke-RestMethod -Method Get -Uri $stateUri -TimeoutSec 30
$after | ConvertTo-Json -Depth 8 | Write-Host
if ($after.activeVisitorFound -eq $true -or $after.archivedVisitorFound -ne $true -or $after.visitState -ne 'CheckedOut' -or
    [string]::IsNullOrWhiteSpace([string]$after.savedCheckOutEventId) -or $after.savedCheckOutMatchesArchive -ne $true) {
    throw 'Post-reconciliation state did not confirm an archived CheckedOut visit and a checkout ledger event whose archive/timestamp evidence agrees. No Genetec writes were made by this script; inspect the local ledger and bridge logs.'
}
Write-Host "PASS: archived checkout reconciled into the local ledger as event '$($after.savedCheckOutEventId)'; check-in/checkout evidence agrees. No Genetec write was sent." -ForegroundColor Green

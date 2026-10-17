[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:5190',
    [ValidatePattern('^VMS-[A-Z0-9-]{1,24}$')]
    [string]$BookingId = 'VMS-SDKTEST-06'
)

$ErrorActionPreference = 'Stop'
Write-Host 'Read-only Genetec Visitor lookup' -ForegroundColor Cyan
Write-Host "Bridge: $BaseUrl"
Write-Host "Synthetic booking key: $BookingId"
Write-Host 'This test only queries Security Center; it does not send any writes.' -ForegroundColor Yellow

$status = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/genetec/status" -TimeoutSec 25
if ($status.connected -ne $true) { throw 'Genetec bridge is not connected. No lookup was completed.' }
if (-not ([string]$status.directoryVersion).StartsWith('5.11')) { throw "Directory version guard failed ($($status.directoryVersion))." }
if ($status.visitorManagementLicensed -ne $true) { throw 'Visitor Management is not reported as licensed.' }

$uri = "$BaseUrl/api/genetec/visitor-booking-lookup?bookingId=$([Uri]::EscapeDataString($BookingId))"
$result = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 35
if ($result.success -ne $true) { throw "Visitor lookup failed: $($result.errorCode) $($result.error)" }
if ($result.writeAttempted -ne $false) { throw 'Diagnostic endpoint did not affirm its read-only contract.' }

Write-Host "PASS: Directory-backed lookup completed. Match count: $($result.matchCount)" -ForegroundColor Green
if ($result.matchCount -gt 1) {
    Write-Host 'DUPLICATES DETECTED: DO NOT RUN VISITOR SYNC UNTIL AN AUTHORISED ADMINISTRATOR RECONCILES THESE RECORDS.' -ForegroundColor Red
} elseif ($result.matchCount -eq 1) {
    Write-Host 'One Visitor matches this booking key. For an active Visitor, checkinDateUtc is derived from ActivationType/ActivationDate; rawEntityCheckinDateUtc is diagnostic only.' -ForegroundColor Yellow
} else {
    Write-Host 'No Visitor matches this booking key. A guarded integration test may create one after the approved environment is confirmed.' -ForegroundColor Yellow
}

[pscustomobject]@{
    BookingId = $result.bookingId
    EntityKey = $result.entityKey
    DirectoryVersion = $result.directoryVersion
    MatchCount = $result.matchCount
    DuplicateDetected = $result.duplicateDetected
    WriteAttempted = $result.writeAttempted
    Matches = @($result.matches)
} | ConvertTo-Json -Depth 5

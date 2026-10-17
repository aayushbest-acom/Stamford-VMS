[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:5190',
    [string]$BookingId = 'VMS-SDKTEST-06'
)

$ErrorActionPreference = 'Stop'
$confirmation = 'SYNC SYNTHETIC STAMFORD VMS VISITOR LIFECYCLE'
$allowedBookingPattern = '^VMS-SDKTEST-[A-Z0-9-]{1,16}$'

if ($BookingId -notmatch $allowedBookingPattern) {
    throw "Safety stop: lifecycle test only permits a synthetic booking ID matching VMS-SDKTEST-*. Received '$BookingId'."
}

Write-Host 'Day 2 native Visitor lifecycle integration test' -ForegroundColor Cyan
Write-Host "Bridge: $BaseUrl"
Write-Host "Synthetic booking: $BookingId"
Write-Host 'Use a fresh synthetic visitor booking for a new lifecycle run; a checked-out booking must not be checked in again.' -ForegroundColor Yellow
Write-Host 'This script exercises the first live lifecycle mapping only on a designated non-production Directory. The operation uses Visitor activation for check-in and Visitor.Checkout() for checkout; a successful independent read-back is required.' -ForegroundColor Yellow

try {
    $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/health" -TimeoutSec 8
    if (-not $health.bridge -and -not $health.service) { throw 'Bridge health response did not contain the expected service identity.' }
    $status = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/genetec/status" -TimeoutSec 20
    if (-not $status.connected) { throw 'Genetec Directory is not connected. No lifecycle writes were sent.' }
    if (-not ([string]$status.directoryVersion).StartsWith('5.11')) { throw "Directory version '$($status.directoryVersion)' is outside the required 5.11 scope. No lifecycle writes were sent." }
    if (-not $status.visitorManagementLicensed) { throw 'Visitor Management is not reported as licensed. No lifecycle writes were sent.' }
}
catch {
    throw "Preflight failed. No lifecycle writes were sent. Details: $($_.Exception.Message)"
}

try {
    $capability = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/genetec/capabilities" -TimeoutSec 8
}
catch {
    Write-Host 'BLOCKED: /api/genetec/capabilities is not available yet. This is expected on the Day 1 baseline. No lifecycle writes were sent.' -ForegroundColor Yellow
    exit 2
}

if (-not $capability.visitorLifecycleSyncSupported -or -not $capability.lifecycleSdkMemberMappingAvailable) {
    Write-Host 'BLOCKED: required native lifecycle SDK members are unavailable. No lifecycle writes were sent.' -ForegroundColor Yellow
    exit 2
}
if (-not $capability.visitorLifecycleSyncEnabled) {
    Write-Host 'BLOCKED: lifecycle write opt-in is disabled. No lifecycle writes were sent.' -ForegroundColor Yellow
    exit 2
}
if (-not $capability.lifecycleOperationMappingVerified) {
    Write-Host 'NOTE: this is the first end-to-end lifecycle mapping test on this Directory. The bridge will report success only after the native SDK state can be read back.' -ForegroundColor Yellow
}

# Read-only preflight: the booking must already map to exactly one active native Visitor.
# Do this before asking for confirmation and before any lifecycle write.
try {
    $escapedBookingId = [Uri]::EscapeDataString($BookingId)
    $lookup = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/genetec/visitor-booking-lookup?bookingId=$escapedBookingId" -TimeoutSec 20
    if ($lookup.matchCount -eq 0) {
        Write-Host "BLOCKED: no native Visitor is mapped to $BookingId. First run Test-NativeVisitorSync.ps1 -BookingId $BookingId and inspect the result. No lifecycle writes were sent." -ForegroundColor Yellow
        exit 2
    }
    if ($lookup.matchCount -ne 1) {
        Write-Host "BLOCKED: Directory lookup found $($lookup.matchCount) Visitors for $BookingId. Reconcile duplicates before lifecycle testing. No lifecycle writes were sent." -ForegroundColor Red
        exit 2
    }
    $matchedVisitor = @($lookup.matches)[0]
    if (-not [string]::IsNullOrWhiteSpace([string]$matchedVisitor.checkinDateUtc) -or
        [string]$matchedVisitor.activationType -ne 'Never') {
        Write-Host "BLOCKED: Genetec already reports activation/check-in state for this Visitor (activationType=$($matchedVisitor.activationType); checkinDateUtc=$($matchedVisitor.checkinDateUtc)). This may be an invited booking created by an older mapping. No lifecycle writes were sent. Reconcile the record and prepare a fresh synthetic booking with activation Never." -ForegroundColor Red
        exit 2
    }
    if ($matchedVisitor.activeLifecycleState -and [string]$matchedVisitor.activeLifecycleState -ne 'pre-registered') {
        Write-Host "BLOCKED: active Visitor lifecycle state is '$($matchedVisitor.activeLifecycleState)'. No lifecycle writes were sent." -ForegroundColor Red
        exit 2
    }
    Write-Host "PASS: read-only preflight found one pre-registered Visitor with activation=Never: $($matchedVisitor.visitorGuid)" -ForegroundColor Green

    # Wait until the scheduled arrival to avoid testing an activation earlier than the appointment
    # or creating a synthetic appointment whose arrival time is already in the past.
    # expectedArrivalLocal is serialized as a true local ISO value (with local offset when available).
    # Parse through DateTimeOffset so a prior UTC value ending in Z cannot be subtracted as if it were local.
    $scheduledArrivalOffset = [DateTimeOffset]::Parse([string]$matchedVisitor.expectedArrivalLocal, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AllowWhiteSpaces)
    $scheduledArrival = $scheduledArrivalOffset.LocalDateTime
    $secondsUntilArrival = ($scheduledArrival - (Get-Date)).TotalSeconds
    if ($secondsUntilArrival -gt 1800) {
        Write-Host ("BLOCKED: scheduled arrival is {0:N1} minutes away, beyond the 30-minute test wait limit. No lifecycle writes were sent." -f ($secondsUntilArrival / 60)) -ForegroundColor Yellow
        exit 2
    }
    if ($secondsUntilArrival -lt -300) {
        Write-Host 'BLOCKED: scheduled arrival is more than five minutes in the past. Prepare a fresh future synthetic appointment; no lifecycle writes were sent.' -ForegroundColor Yellow
        exit 2
    }
    if ($secondsUntilArrival -gt 0) {
        $waitSeconds = [int][Math]::Ceiling($secondsUntilArrival)
        Write-Host ("Waiting {0} second(s) until the booked arrival time before requesting lifecycle writes..." -f $waitSeconds) -ForegroundColor Yellow
        Start-Sleep -Seconds $waitSeconds
    }

    # Re-query after any wait. Ensure the same Visitor is still unique and pre-registered before prompting for writes.
    $lookup = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/genetec/visitor-booking-lookup?bookingId=$escapedBookingId" -TimeoutSec 20
    if ($lookup.success -ne $true -or $lookup.matchCount -ne 1) {
        Write-Host 'BLOCKED: final pre-write lookup did not return exactly one Visitor. No lifecycle writes were sent.' -ForegroundColor Red
        exit 2
    }
    $finalVisitor = @($lookup.matches)[0]
    if ([string]$finalVisitor.visitorGuid -ne [string]$matchedVisitor.visitorGuid -or
        [string]$finalVisitor.activationType -ne 'Never' -or
        -not [string]::IsNullOrWhiteSpace([string]$finalVisitor.checkinDateUtc) -or
        ($finalVisitor.activeLifecycleState -and [string]$finalVisitor.activeLifecycleState -ne 'pre-registered')) {
        Write-Host 'BLOCKED: final pre-write state differs from the pre-registered Visitor verified before waiting. No lifecycle writes were sent.' -ForegroundColor Red
        exit 2
    }
}
catch {
    throw "Read-only Visitor preflight failed. No lifecycle writes were sent. Details: $($_.Exception.Message)"
}

Write-Host 'WARNING: this will mutate one synthetic Visitor lifecycle in the NON-PRODUCTION Directory.' -ForegroundColor Yellow
$typed = Read-Host 'Type SYNC SYNTHETIC STAMFORD VMS VISITOR LIFECYCLE to continue'
if ($typed -cne $confirmation) {
    Write-Host 'Cancelled. No lifecycle writes were sent.' -ForegroundColor Yellow
    exit 3
}

function Invoke-LifecycleEvent {
    param(
        [Parameter(Mandatory = $true)] [string]$EventId,
        [Parameter(Mandatory = $true)] [ValidateSet('CheckIn', 'CheckOut')] [string]$EventType,
        [Parameter(Mandatory = $true)] [string]$EventAtUtc,
        [Parameter(Mandatory = $true)] [ValidateSet('Inside', 'Checked out')] [string]$VmsStatus
    )

    $body = @{
        Confirmation = $confirmation
        BookingId = $BookingId
        EventId = $EventId
        EventType = $EventType
        EventAtUtc = $EventAtUtc
        VmsStatus = $VmsStatus
    } | ConvertTo-Json -Depth 5

    try {
        $result = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/genetec/visitor-lifecycle" -ContentType 'application/json' -Body $body -TimeoutSec 45
    }
    catch {
        $requestErrorMessage = $_.Exception.Message
        Write-Host "$EventType request failed. No automatic lifecycle retry will be sent." -ForegroundColor Red
        Write-Host 'Running read-only lifecycle state inspection so we can distinguish active check-in from an already-archived checkout...' -ForegroundColor Yellow
        try {
            $stateUrl = "$BaseUrl/api/genetec/visitor-lifecycle-state?bookingId=$([Uri]::EscapeDataString($BookingId))"
            $state = Invoke-RestMethod -Method Get -Uri $stateUrl -TimeoutSec 25
            $state | ConvertTo-Json -Depth 8 | Write-Host
        }
        catch {
            Write-Warning "Read-only lifecycle state inspection also failed: $($_.Exception.Message)"
        }
        throw "${EventType} failed for event '$EventId'. Do not rerun this lifecycle sequence until the read-only state and bridge log are reviewed. Original error: $requestErrorMessage"
    }
    if (-not $result.success) { throw "$EventType failed for event '$EventId'. Inspect bridge logs and Security Center before retrying." }
    if (-not $result.readBackVerified) { throw "$EventType did not provide verified read-back. Inspect Genetec before retrying." }
    if ($result.bookingId -ne $BookingId) { throw "Response booking ID mismatch for '$EventId'." }
    if ($result.eventId -ne $EventId) { throw "Response event ID mismatch for '$EventId'." }
    if ($result.eventType -ne $EventType) { throw "Response event type mismatch for '$EventId'." }
    if ([string]::IsNullOrWhiteSpace([string]$result.visitorGuid)) { throw "Response lacked the actual Visitor GUID for '$EventId'." }
    return $result
}

$now = [DateTime]::UtcNow
$checkInAt = $now.ToString('o')
$checkOutAt = $null
$runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$checkInId = "$BookingId-$runId-CHECKIN"
$checkOutId = "$BookingId-$runId-CHECKOUT"

Write-Host '1/4 Check-in event...' -ForegroundColor Cyan
$checkIn = Invoke-LifecycleEvent -EventId $checkInId -EventType 'CheckIn' -EventAtUtc $checkInAt -VmsStatus 'Inside'
Write-Host "PASS: CheckIn verified. Visitor GUID: $($checkIn.visitorGuid); operation: $($checkIn.operationId)" -ForegroundColor Green

Write-Host '2/4 Retry the identical check-in event...' -ForegroundColor Cyan
$checkInRetry = Invoke-LifecycleEvent -EventId $checkInId -EventType 'CheckIn' -EventAtUtc $checkInAt -VmsStatus 'Inside'
if ($checkInRetry.visitorGuid -ne $checkIn.visitorGuid) { throw 'Idempotent check-in retry returned a different Visitor GUID.' }
if (-not $checkInRetry.alreadyApplied) { throw 'Exact check-in replay was not reported as alreadyApplied. Stop and inspect the durable event ledger before proceeding.' }
Write-Host 'PASS: CheckIn retry returned the same Visitor GUID.' -ForegroundColor Green

Write-Host '3/4 Check-out event...' -ForegroundColor Cyan
$checkOutAt = [DateTime]::UtcNow.ToString('o')
$checkOut = Invoke-LifecycleEvent -EventId $checkOutId -EventType 'CheckOut' -EventAtUtc $checkOutAt -VmsStatus 'Checked out'
if ($checkOut.visitorGuid -ne $checkIn.visitorGuid) { throw 'Check-out returned a different Visitor GUID than check-in.' }
Write-Host "PASS: CheckOut verified. Visitor GUID: $($checkOut.visitorGuid); operation: $($checkOut.operationId)" -ForegroundColor Green

Write-Host '4/4 Retry the identical check-out event...' -ForegroundColor Cyan
$checkOutRetry = Invoke-LifecycleEvent -EventId $checkOutId -EventType 'CheckOut' -EventAtUtc $checkOutAt -VmsStatus 'Checked out'
if ($checkOutRetry.visitorGuid -ne $checkIn.visitorGuid) { throw 'Idempotent check-out retry returned a different Visitor GUID.' }
if (-not $checkOutRetry.alreadyApplied) { throw 'Exact checkout replay was not reported as alreadyApplied. Stop and inspect the durable event ledger before proceeding.' }
Write-Host 'PASS: CheckOut retry returned the same Visitor GUID.' -ForegroundColor Green

Write-Host ''
Write-Host 'PASS: Day 2 lifecycle event and duplicate-retry checks completed.' -ForegroundColor Green
Write-Host 'Inspect the native Visitor in Security Center and retain the redacted JSON as acceptance evidence.' -ForegroundColor Yellow
[pscustomobject]@{
    bookingId = $BookingId
    visitorGuid = $checkIn.visitorGuid
    checkInOperationId = $checkIn.operationId
    checkOutOperationId = $checkOut.operationId
    checkInReadBackVerified = $checkIn.readBackVerified
    checkOutReadBackVerified = $checkOut.readBackVerified
    credentialsChanged = $false
    accessRightsChanged = $false
} | ConvertTo-Json -Depth 5

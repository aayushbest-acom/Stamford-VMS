[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:5190',
    [ValidatePattern('^VMS-SDKTEST-[A-Z0-9-]{1,16}$')]
    [string]$BookingId = 'VMS-SDKTEST-06',
    [switch]$PrepareForLifecycleTest,
    [ValidateRange(5, 60)] [int]$LifecycleLeadMinutes = 20
)

$ErrorActionPreference = 'Stop'
$confirm = Read-Host 'This creates or updates ONE synthetic Visitor in Genetec. Type RUN SYNTHETIC VISITOR INTEGRATION TEST to continue'
if ($confirm -cne 'RUN SYNTHETIC VISITOR INTEGRATION TEST') {
    Write-Host 'Cancelled; no request was sent.' -ForegroundColor Yellow
    exit 2
}

$status = Invoke-RestMethod -Method Get -Uri "$baseUrl/api/genetec/status" -TimeoutSec 35
if ($status.connected -ne $true) { throw 'Genetec bridge is not connected. No visitor write was attempted.' }
if (-not ([string]$status.directoryVersion).StartsWith('5.11')) { throw "Directory version guard failed ($($status.directoryVersion)). No visitor write was attempted." }
if ($status.visitorManagementLicensed -ne $true) { throw 'Visitor Management is not reported as licensed. No visitor write was attempted.' }
if ($status.visitorSyncEnabled -ne $true) { throw 'Native Visitor sync is not explicitly enabled at bridge startup. No visitor write was attempted.' }

# Before any write, perform a Directory-backed lookup using the stable entity key. This prevents
# the test from adding another duplicate when earlier runs created more than one Visitor.
$lookupUri = "$BaseUrl/api/genetec/visitor-booking-lookup?bookingId=$([Uri]::EscapeDataString($BookingId))"
$lookup = Invoke-RestMethod -Method Get -Uri $lookupUri -TimeoutSec 35
if ($lookup.success -ne $true) { throw 'Preflight Visitor lookup failed. No visitor write was attempted.' }
if ([int]$lookup.matchCount -gt 1) {
    Write-Host "STOP: more than one Genetec Visitor matches $BookingId. No write was sent. Reconcile these records in Security Center first:" -ForegroundColor Red
    @($lookup.matches) | ForEach-Object { Write-Host ("  GUID: {0} | Name: {1}" -f $_.visitorGuid, $_.entityName) -ForegroundColor Yellow }
    throw 'Duplicate stable-key Visitors were found. No visitor write was attempted.'
}
if ($BookingId -eq 'VMS-SDKTEST-01' -and [int]$lookup.matchCount -eq 0) {
    throw 'Safety stop: previous logs showed duplicate Visitors created for VMS-SDKTEST-01, but the server-side lookup returned zero. Do not write under this key until lookup behavior and existing records are manually verified. No visitor write was attempted.'
}
if ([int]$lookup.matchCount -eq 1) {
    $existingVisitor = @($lookup.matches)[0]
    if (-not [string]::IsNullOrWhiteSpace([string]$existingVisitor.checkinDateUtc) -or
        [string]$existingVisitor.activationType -ne 'Never') {
        throw "Safety stop: $BookingId already has native activation/check-in state (activationType=$($existingVisitor.activationType); checkinDateUtc=$($existingVisitor.checkinDateUtc)). Do not overwrite it automatically. Reconcile in Security Center and use a fresh synthetic booking ID. No visitor write was attempted."
    }
}

# Booking ID is constrained to a synthetic test namespace. Reusing it demonstrates idempotency only while it remains pre-registered.
# For lifecycle testing, create a future appointment and let the lifecycle harness wait until arrival.
# The default remains tomorrow for sync-only tests.
if ($PrepareForLifecycleTest) {
    # Genetec validates the expected-arrival/expiration window against the current time. The safe
    # default is twenty minutes; callers can use a shorter lead for a supervised rapid retest, but
    # never less than five minutes. If the window crosses midnight, ParseVisitWindow advances departure.
    $arrival = (Get-Date).AddMinutes($LifecycleLeadMinutes)
    $departure = $arrival.AddMinutes(70)
    $visitDate = $arrival.Date.ToString('yyyy-MM-dd')
    $visitWindow = '{0}-{1}' -f $arrival.ToString('HH:mm'), $departure.ToString('HH:mm')
    Write-Host "Preparing a future synthetic appointment for lifecycle testing (+$LifecycleLeadMinutes minutes): $visitDate $visitWindow" -ForegroundColor Yellow
} else {
    $visitDate = (Get-Date).Date.AddDays(1).ToString('yyyy-MM-dd')
    $visitWindow = '09:00-10:00'
}
function Assert-FreshPreRegistrationReadBack {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$ExpectedGuid,
        [Parameter(Mandatory = $true)][string]$ExpectedFirstName
    )

    # This endpoint opens a fresh SDK session. Validate Directory state before issuing the next write,
    # so a committed-but-invalid creation is not followed by an automatic retry that compounds the issue.
    $fresh = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/genetec/visitor-booking-lookup?bookingId=$([Uri]::EscapeDataString($BookingId))" -TimeoutSec 35
    if ($fresh.success -ne $true -or [int]$fresh.matchCount -ne 1) {
        throw "${Stage}: fresh Directory lookup did not return exactly one Visitor. Do not retry; inspect Genetec first."
    }

    $record = @($fresh.matches)[0]
    if ([string]$record.visitorGuid -ne $ExpectedGuid) {
        throw "${Stage}: fresh Directory lookup returned a different Visitor GUID. Do not retry; inspect Genetec first."
    }
    if ([string]$record.activationType -ne 'Never' -or
        -not [string]::IsNullOrWhiteSpace([string]$record.checkinDateUtc) -or
        ([string]$record.departureLocal).StartsWith('0001-01-01', [StringComparison]::Ordinal) -or
        ([string]$record.expectedArrivalLocal).StartsWith('0001-01-01', [StringComparison]::Ordinal) -or
        ($record.activeLifecycleState -and [string]$record.activeLifecycleState -ne 'pre-registered')) {
        throw "${Stage}: fresh Directory read-back shows the Visitor is not safely pre-registered (activationType=$($record.activationType); checkinDateUtc=$($record.checkinDateUtc); expectedArrivalLocal=$($record.expectedArrivalLocal); departureLocal=$($record.departureLocal)). Do not retry or run lifecycle writes; inspect/reconcile the record in Genetec."
    }
    if ([string]$record.firstName -ne $ExpectedFirstName) {
        throw "${Stage}: fresh Directory name read-back did not match '$ExpectedFirstName'. Do not retry; inspect Genetec first."
    }
    Write-Host "PASS: fresh Directory pre-registration read-back after $Stage (GUID $ExpectedGuid; rawEntityCheckinDateUtc=$($record.rawEntityCheckinDateUtc) is diagnostic only)." -ForegroundColor Green
}

$payload = [ordered]@{
    Confirmation = 'SYNC_SYNTHETIC_STAMFORD_VMS_VISITOR'
    BookingId = $BookingId
    FirstName = 'Stamford VMS'
    LastName = 'Synthetic Test Visitor'
    VisitDate = $visitDate
    VisitWindow = $visitWindow
    Status = 'Invited'
} | ConvertTo-Json -Compress

Write-Host 'Submitting first visitor booking operation...' -ForegroundColor Cyan
$first = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/genetec/visitor-booking" -ContentType 'application/json' -Body $payload -TimeoutSec 60
if ($first.success -ne $true -or [string]::IsNullOrWhiteSpace([string]$first.visitorGuid)) {
    throw 'First operation did not return verified visitor success. Inspect Genetec and bridge logs before retrying.'
}
if ($first.scheduleReadbackVerified -ne $true -or $first.nameReadbackVerified -ne $true -or $first.serverReportReadbackVerified -ne $true) {
    throw 'First operation did not confirm server query, name and schedule read-back. Inspect Genetec before continuing.'
}
Assert-FreshPreRegistrationReadBack -Stage 'initial create/upsert' -ExpectedGuid ([string]$first.visitorGuid) -ExpectedFirstName 'Stamford VMS'

Write-Host 'Repeating the exact same booking to verify safe retry...' -ForegroundColor Cyan
$retry = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/genetec/visitor-booking" -ContentType 'application/json' -Body $payload -TimeoutSec 60
if ($retry.success -ne $true -or [string]$retry.visitorGuid -ne [string]$first.visitorGuid) {
    throw 'The retry did not return the same Visitor GUID. Stop and investigate for duplicate entities before further testing.'
}
if ($retry.scheduleReadbackVerified -ne $true -or $retry.nameReadbackVerified -ne $true -or $retry.serverReportReadbackVerified -ne $true) {
    throw 'The retry did not confirm server query, name and schedule read-back. Inspect Genetec before continuing.'
}
Assert-FreshPreRegistrationReadBack -Stage 'exact retry' -ExpectedGuid ([string]$first.visitorGuid) -ExpectedFirstName 'Stamford VMS'

Write-Host 'Updating the same booking key to verify field updates do not create another Visitor...' -ForegroundColor Cyan
$updatedObject = $payload | ConvertFrom-Json
$updatedObject.FirstName = 'Stamford VMS Updated'
if ($PrepareForLifecycleTest) {
    # Preserve the future appointment window so invitation upsert stays pre-registered and
    # leaves enough time to start the lifecycle test without the arrival drifting into the past.
    $updatedObject.VisitWindow = $visitWindow
} else {
    $updatedObject.VisitWindow = '10:00-11:00'
}
$updatedPayload = $updatedObject | ConvertTo-Json -Compress
$updated = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/genetec/visitor-booking" -ContentType 'application/json' -Body $updatedPayload -TimeoutSec 60
if ($updated.success -ne $true -or [string]$updated.visitorGuid -ne [string]$first.visitorGuid) {
    throw 'The changed booking did not return the original Visitor GUID. Stop and investigate before further testing.'
}
if ($updated.operation -ne 'update-native-visitor' -or $updated.firstName -ne $updatedObject.FirstName -or $updated.nameReadbackVerified -ne $true -or $updated.scheduleReadbackVerified -ne $true -or $updated.serverReportReadbackVerified -ne $true) {
    throw 'The update did not return the expected name, update outcome, and read-back flags. Inspect Genetec before further testing.'
}
Assert-FreshPreRegistrationReadBack -Stage 'field update' -ExpectedGuid ([string]$first.visitorGuid) -ExpectedFirstName 'Stamford VMS Updated'

Write-Host ''
Write-Host 'PASS: create/upsert, exact retry, and field update all returned the same native Genetec Visitor GUID.' -ForegroundColor Green
$parsedPayload = $payload | ConvertFrom-Json
[pscustomobject]@{
    BookingId = $parsedPayload.BookingId
    FirstOperation = $first.operation
    RetryOperation = $retry.operation
    UpdateOperation = $updated.operation
    VisitorGuid = $updated.visitorGuid
    DirectoryVersion = $updated.directoryVersion
    NameReadbackVerified = $updated.nameReadbackVerified
    ScheduleReadbackVerified = $updated.scheduleReadbackVerified
    ServerReportReadbackVerified = $updated.serverReportReadbackVerified
    FirstOperationId = $first.operationId
    RetryOperationId = $retry.operationId
    UpdateOperationId = $updated.operationId
} | Format-List
Write-Host 'This test creates/updates only one synthetic Visitor. It does not create credentials, assign escorts/groups, or change access rights.' -ForegroundColor Yellow

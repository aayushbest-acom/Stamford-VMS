$ErrorActionPreference = 'Stop'
$baseUrl = 'http://127.0.0.1:5190'
Write-Host 'Checking the live read-only Genetec bridge...' -ForegroundColor Cyan
$status = Invoke-RestMethod -Method Get -Uri "$baseUrl/api/genetec/status" -TimeoutSec 35
$checks = @(
    [pscustomobject]@{ Name = 'Authenticated connection'; Pass = ($status.connected -eq $true -and $status.mode -eq 'read-only'); Detail = "$($status.mode) / connected=$($status.connected)" },
    [pscustomobject]@{ Name = 'Security Center Directory 5.11'; Pass = ([string]$status.directoryVersion).StartsWith('5.11'); Detail = [string]$status.directoryVersion },
    [pscustomobject]@{ Name = 'Cardholder-group query'; Pass = ($status.queryStatus -eq 'success' -and $null -ne $status.cardholderGroupsCount); Detail = "query=$($status.queryStatus); count=$($status.cardholderGroupsCount)" },
    [pscustomobject]@{ Name = 'Visitor Management license'; Pass = ($status.visitorManagementLicensed -eq $true); Detail = [string]$status.visitorManagementLicensed },
    [pscustomobject]@{ Name = 'Native Visitor sync opted in'; Pass = ($status.visitorSyncEnabled -eq $true); Detail = [string]$status.visitorSyncEnabled }
)
foreach ($check in $checks) {
    if ($check.Pass) {
        Write-Host ("PASS: {0} - {1}" -f $check.Name, $check.Detail) -ForegroundColor Green
    } else {
        Write-Host ("FAIL: {0} - {1}" -f $check.Name, $check.Detail) -ForegroundColor Red
    }
}
if (($checks | Where-Object { -not $_.Pass }).Count -gt 0) {
    throw 'One or more bridge readiness checks failed. No write requests were sent.'
}
Write-Host ''
Write-Host 'PASS: Day 1 read-only bridge gates passed. This script sent no writes.' -ForegroundColor Green
$status | ConvertTo-Json -Depth 5

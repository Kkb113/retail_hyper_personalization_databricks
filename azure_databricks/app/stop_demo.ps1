param([Parameter(Mandatory=$true)][long]$DeadlineUnix)
$ErrorActionPreference = 'Stop'
$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
if ($DeadlineUnix -gt ($now + 1800)) { throw 'Deadline exceeds 30 minutes' }
# Host and target IDs are frozen by the validated deployment, never supplied by callers.
$workspaceHost = 'https://adb-7405618180989330.10.azuredatabricks.net'
$warehouseId = '__WAREHOUSE_ID__'
if ($warehouseId -notmatch '^[a-f0-9]{16}$') { throw 'Invalid warehouse binding' }
function Invoke-Workspace([string]$method, [string]$path) {
    $tokenReply = Invoke-RestMethod -Method Get -Uri ($env:IDENTITY_ENDPOINT + '?resource=2ff814a6-3304-4ab8-85cb-cd0e6f879c1d') -Headers @{'X-IDENTITY-HEADER'=$env:IDENTITY_HEADER;Metadata='True'} -TimeoutSec 20 -MaximumRedirection 0
    try {
        return Invoke-RestMethod -Method $method -Uri ($workspaceHost + $path) -Headers @{Authorization=('Bearer ' + $tokenReply.access_token)} -TimeoutSec 20 -MaximumRedirection 0
    } finally { $tokenReply = $null }
}
$targets = @(
    @{Name='app';Path='/api/2.0/apps/retail-hp-poc-app';Stop='/stop'},
    @{Name='warehouse';Path=('/api/2.0/sql/warehouses/' + $warehouseId);Stop='/stop'},
    @{Name='recommender';Path='/api/2.0/serving-endpoints/retail-hp-poc-recommender';Stop='/config:stop'}
)
function Get-State($target) {
    $response = Invoke-Workspace 'GET' $target.Path
    if ($target.Name -eq 'app') { return $response.compute_status.state }
    if ($target.Name -eq 'warehouse') { return $response.state }
    return $response.state.suspend
}
# Prove identity/metadata access before announcing readiness. Never print tokens or bodies.
foreach ($target in $targets) { $null = Get-State $target }
Write-Output 'CONTROLLER_ARMED'
while ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -lt $DeadlineUnix) { Start-Sleep -Seconds 5 }
$end = [DateTimeOffset]::UtcNow.AddMinutes(5)
do {
    $allStopped = $true
    foreach ($target in $targets) {
        try {
            $state = Get-State $target
            if ($state -ne 'STOPPED') {
                $allStopped = $false
                $null = Invoke-Workspace 'POST' ($target.Path + $target.Stop)
            }
        } catch {
            $allStopped = $false
            Write-Output ('STOP_RETRY_' + $target.Name)
        }
    }
    if ($allStopped) { Write-Output 'ALL_TARGETS_STOPPED'; return }
    Start-Sleep -Seconds 10
} while ([DateTimeOffset]::UtcNow -lt $end)
throw 'STOP_NOT_VERIFIED: operator intervention required'

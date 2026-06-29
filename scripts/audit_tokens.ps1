# Count requests by model/adapter from gateway.out.log
$lines = Get-Content 'C:\Tools\gateway\logs\gateway.out.log' -Tail 5000
$byModel = @{}
$summarizer = 0
$totalRequests = 0
foreach ($l in $lines) {
    try {
        $o = $l | ConvertFrom-Json -ErrorAction Stop
        if ($o.msg -eq 'request_done') {
            $k = "$($o.model) -> $($o.adapter)"
            $byModel[$k] = ($byModel[$k] ?? 0) + 1
            $totalRequests++
        }
        if ($o.msg -match 'summarizer') {
            $summarizer++
        }
    } catch {}
}

Write-Host "=== Last 5000 log lines ===" -ForegroundColor Cyan
Write-Host "Total request_done events: $totalRequests"
Write-Host "Summarizer log lines:      $summarizer"
Write-Host ""
Write-Host "=== By model / adapter ===" -ForegroundColor Cyan
$byModel.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object {
    Write-Host ("  {0,-40} {1,5}" -f $_.Key, $_.Value)
}

Write-Host ""
Write-Host "=== Time span of these log lines ===" -ForegroundColor Cyan
$first = (Get-Content 'C:\Tools\gateway\logs\gateway.out.log' -Tail 5000)[0] | ConvertFrom-Json -ErrorAction SilentlyContinue
$last  = (Get-Content 'C:\Tools\gateway\logs\gateway.out.log' -Tail 1)   | ConvertFrom-Json -ErrorAction SilentlyContinue
if ($first -and $last) {
    Write-Host "  first ts: $($first.ts)"
    Write-Host "  last ts:  $($last.ts)"
}

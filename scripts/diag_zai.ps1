$tok = (Invoke-WebRequest 'http://127.0.0.1:8787/v1/admin/dev_token' -UseBasicParsing | ConvertFrom-Json).token

Write-Host '=== /health shows zai upstream now? ===' -ForegroundColor Cyan
$r = Invoke-WebRequest 'http://127.0.0.1:8787/health' -UseBasicParsing -TimeoutSec 5
$h = $r.Content | ConvertFrom-Json
$h.upstreams | Format-Table -AutoSize

Write-Host ''
Write-Host '=== /v1/models (gateway, all 8) ===' -ForegroundColor Cyan
$r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/models' -Headers @{Authorization="Bearer $tok"} -UseBasicParsing -TimeoutSec 5
($r.Content | ConvertFrom-Json).data.id

Write-Host ''
Write-Host '=== headroom-zai direct probe (skipping gateway) ===' -ForegroundColor Cyan
try {
    $body = '{"model":"glm-4.6","messages":[{"role":"user","content":"hi"}],"max_tokens":20,"stream":false}'
    $r = Invoke-WebRequest 'http://127.0.0.1:8793/v1/chat/completions' -Method POST -Headers @{ "Content-Type"="application/json" } -Body $body -UseBasicParsing -TimeoutSec 30
    Write-Host "  status: $($r.StatusCode)"
    Write-Host "  body: $($r.Content.Substring(0, [Math]::Min(300, $r.Content.Length)))"
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
    if ($_.Exception.Response) {
        Write-Host "  upstream body:"
        (New-Object System.IO.StreamReader $_.Exception.Response.GetResponseStream()).ReadToEnd()
    }
}

Write-Host ''
Write-Host '=== glm-5.2 via gateway (zai adapter -> 8793 -> api.z.ai) ===' -ForegroundColor Cyan
try {
    $body = '{"model":"glm-5.2","messages":[{"role":"user","content":"hi in 3 words"}],"max_tokens":50,"stream":false}'
    $r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/chat/completions' -Method POST -Headers @{ Authorization="Bearer $tok"; "Content-Type"="application/json" } -Body $body -UseBasicParsing -TimeoutSec 45
    Write-Host "  status: $($r.StatusCode)"
    Write-Host "  body: $($r.Content.Substring(0, [Math]::Min(500, $r.Content.Length)))"
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
    if ($_.Exception.Response) {
        Write-Host "  upstream body:"
        (New-Object System.IO.StreamReader $_.Exception.Response.GetResponseStream()).ReadToEnd()
    }
}

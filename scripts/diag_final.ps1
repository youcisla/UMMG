$tok = (Invoke-WebRequest 'http://127.0.0.1:8787/v1/admin/dev_token' -UseBasicParsing | ConvertFrom-Json).token

Write-Host '=== /health ===' -ForegroundColor Cyan
$h = (Invoke-WebRequest 'http://127.0.0.1:8787/health' -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
"overall: $($h.ok)"
$h.upstreams | Format-Table -AutoSize
$h | ConvertTo-Json -Depth 4

Write-Host ''
Write-Host '=== /v1/models (the list Claude Code sees) ===' -ForegroundColor Cyan
$r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/models' -Headers @{Authorization="Bearer $tok"} -UseBasicParsing -TimeoutSec 5
$r.Content | ConvertFrom-Json | ConvertTo-Json -Depth 4

Write-Host ''
Write-Host '=== Port 8793 should now be free (no headroom-zai running) ===' -ForegroundColor Cyan
Get-NetTCPConnection -LocalPort 8793 -ErrorAction SilentlyContinue | Format-Table -AutoSize
if (-not (Get-NetTCPConnection -LocalPort 8793 -ErrorAction SilentlyContinue)) { Write-Host "  (free)" -ForegroundColor Green }

Write-Host ''
Write-Host '=== glm-5.2 via gateway (zai adapter -> direct HTTPS) ===' -ForegroundColor Cyan
try {
    $body = '{"model":"glm-5.2","messages":[{"role":"user","content":"hi in 3 words"}],"max_tokens":50}'
    $r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/chat/completions' -Method POST -Headers @{ Authorization="Bearer $tok"; "Content-Type"="application/json" } -Body $body -UseBasicParsing -TimeoutSec 45
    Write-Host "  status: $($r.StatusCode)"
    Write-Host "  body: $($r.Content.Substring(0, [Math]::Min(400, $r.Content.Length)))"
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
    if ($_.Exception.Response) {
        $sr = New-Object System.IO.StreamReader $_.Exception.Response.GetResponseStream()
        Write-Host "  upstream body:"
        $sr.ReadToEnd()
    }
}

Write-Host ''
Write-Host '=== minimax-m3 (control: should still work) ===' -ForegroundColor Cyan
try {
    $body = '{"model":"minimax-m3","messages":[{"role":"user","content":"say hi"}],"max_tokens":50}'
    $r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/chat/completions' -Method POST -Headers @{ Authorization="Bearer $tok"; "Content-Type"="application/json" } -Body $body -UseBasicParsing -TimeoutSec 45
    Write-Host "  status: $($r.StatusCode)"
    Write-Host "  body: $($r.Content.Substring(0, [Math]::Min(200, $r.Content.Length)))"
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
}

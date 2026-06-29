$tok = (Invoke-WebRequest 'http://127.0.0.1:8787/v1/admin/dev_token' -UseBasicParsing | ConvertFrom-Json).token

# Pull the gateway's advertised models
$models = (Invoke-WebRequest 'http://127.0.0.1:8787/v1/models' -Headers @{Authorization="Bearer $tok"} -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json

Write-Host "=== /v1/models (gateway advertises) ===" -ForegroundColor Cyan
Write-Host ""
foreach ($m in $models.data) {
    Write-Host "  $($m.id)" -ForegroundColor White
}

Write-Host ""
Write-Host "=== Health per upstream ===" -ForegroundColor Cyan
$h = (Invoke-WebRequest 'http://127.0.0.1:8787/health' -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
foreach ($up in $h.upstreams.PSObject.Properties) {
    $ok = $up.Value.ok
    $col = if ($ok) { 'Green' } else { 'Red' }
    Write-Host "  $($up.Name): $(if ($ok) {'OK'} else {'DEGRADED'})  ($($up.Value.url))" -ForegroundColor $col
}

Write-Host ""
Write-Host "=== Quick ping each model with max_tokens=20 ===" -ForegroundColor Cyan
foreach ($m in $models.data) {
    $name = $m.id
    $body = "{`"model`":`"$name`",`"messages`":[{`"role`":`"user`",`"content`":`"ping`"}],`"max_tokens`":20,`"stream`":false}"
    try {
        $r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/chat/completions' -Method POST -Headers @{ Authorization="Bearer $tok"; "Content-Type"="application/json" } -Body $body -UseBasicParsing -TimeoutSec 30
        $code = $r.StatusCode
        $obj = $r.Content | ConvertFrom-Json
        $got = if ($obj.choices[0].message.content) { 'OK' } else { 'OK (empty content, model echoed reasoning)' }
        Write-Host "  $name -> $code $got" -ForegroundColor Green
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        $b = ''
        try { $b = (New-Object System.IO.StreamReader $_.Exception.Response.GetResponseStream()).ReadToEnd() } catch {}
        $b1 = $b.Substring(0, [Math]::Min(80, $b.Length))
        if ($code -ge 400 -and $b1 -match 'credit') {
            Write-Host "  $name -> $code (Anthropic credit error)" -ForegroundColor Yellow
        } elseif ($code -ge 400 -and $b1 -match 'auth') {
            Write-Host "  $name -> $code (missing/empty key in .env)" -ForegroundColor Yellow
        } else {
            Write-Host "  $name -> $code : $b1" -ForegroundColor Red
        }
    }
}

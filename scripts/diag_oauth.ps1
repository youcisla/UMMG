$tok = (Invoke-WebRequest 'http://127.0.0.1:8787/v1/admin/dev_token' -UseBasicParsing | ConvertFrom-Json).token

Write-Host '=== claude-sonnet via gateway (Pro OAuth) ===' -ForegroundColor Cyan
try {
    $body = '{"model":"claude-sonnet","messages":[{"role":"user","content":"say hi in 4 words"}],"max_tokens":40}'
    $r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/chat/completions' -Method POST -Headers @{ Authorization="Bearer $tok"; "Content-Type"="application/json" } -Body $body -UseBasicParsing -TimeoutSec 45
    Write-Host "  status: $($r.StatusCode) OK" -ForegroundColor Green
    $obj = $r.Content | ConvertFrom-Json
    Write-Host "  text: $($obj.choices[0].message.content)"
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    $b = (New-Object System.IO.StreamReader $_.Exception.Response.GetResponseStream()).ReadToEnd()
    Write-Host "  status: $code"
    Write-Host "  body: $($b.Substring(0, [Math]::Min(300, $b.Length)))"
}

Write-Host ''
Write-Host '=== claude-opus via gateway (Pro OAuth) ===' -ForegroundColor Cyan
try {
    $body = '{"model":"claude-opus","messages":[{"role":"user","content":"say hi in 4 words"}],"max_tokens":40}'
    $r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/chat/completions' -Method POST -Headers @{ Authorization="Bearer $tok"; "Content-Type"="application/json" } -Body $body -UseBasicParsing -TimeoutSec 60
    Write-Host "  status: $($r.StatusCode) OK" -ForegroundColor Green
    $obj = $r.Content | ConvertFrom-Json
    Write-Host "  text: $($obj.choices[0].message.content)"
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    $b = (New-Object System.IO.StreamReader $_.Exception.Response.GetResponseStream()).ReadToEnd()
    Write-Host "  status: $code"
    Write-Host "  body: $($b.Substring(0, [Math]::Min(300, $b.Length)))"
}

Write-Host ''
Write-Host '=== claude-fable-5 via gateway (Pro OAuth) ===' -ForegroundColor Cyan
try {
    $body = '{"model":"claude-fable-5","messages":[{"role":"user","content":"say hi in 4 words"}],"max_tokens":40}'
    $r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/chat/completions' -Method POST -Headers @{ Authorization="Bearer $tok"; "Content-Type"="application/json" } -Body $body -UseBasicParsing -TimeoutSec 45
    Write-Host "  status: $($r.StatusCode) OK" -ForegroundColor Green
    $obj = $r.Content | ConvertFrom-Json
    Write-Host "  text: $($obj.choices[0].message.content)"
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    $b = (New-Object System.IO.StreamReader $_.Exception.Response.GetResponseStream()).ReadToEnd()
    Write-Host "  status: $code"
    Write-Host "  body: $($b.Substring(0, [Math]::Min(300, $b.Length)))"
}

Write-Host ''
Write-Host '=== glm-5.2 via gateway (ZAI direct) ===' -ForegroundColor Cyan
try {
    $body = '{"model":"glm-5.2","messages":[{"role":"user","content":"say hi in 4 words"}],"max_tokens":40}'
    $r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/chat/completions' -Method POST -Headers @{ Authorization="Bearer $tok"; "Content-Type"="application/json" } -Body $body -UseBasicParsing -TimeoutSec 45
    Write-Host "  status: $($r.StatusCode) OK" -ForegroundColor Green
    $obj = $r.Content | ConvertFrom-Json
    Write-Host "  text: $($obj.choices[0].message.content)"
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    $b = (New-Object System.IO.StreamReader $_.Exception.Response.GetResponseStream()).ReadToEnd()
    Write-Host "  status: $code"
    Write-Host "  body: $($b.Substring(0, [Math]::Min(300, $b.Length)))"
}

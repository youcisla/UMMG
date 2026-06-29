# Diagnostic: dump raw /v1/models + Claude Code env
$ErrorActionPreference = 'Stop'

$tok = (Invoke-WebRequest 'http://127.0.0.1:8787/v1/admin/dev_token' -UseBasicParsing | ConvertFrom-Json).token

Write-Host '=== Raw /v1/models response (no auth) ===' -ForegroundColor Cyan
$r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/models' -UseBasicParsing -TimeoutSec 5
Write-Host "status: $($r.StatusCode)"
Write-Host $r.Content

Write-Host ''
Write-Host '=== With auth header (what Claude Code sends) ===' -ForegroundColor Cyan
$r = Invoke-WebRequest 'http://127.0.0.1:8787/v1/models' -Headers @{Authorization="Bearer $tok"} -UseBasicParsing -TimeoutSec 5
Write-Host "status: $($r.StatusCode)"
$r.Content | ConvertFrom-Json | ConvertTo-Json -Depth 6

Write-Host ''
Write-Host '=== /v1/models as Claude Code would call it (paths Claude Code uses) ===' -ForegroundColor Cyan
foreach ($p in '/v1/models', '/v1/messages') {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:8787$p" -UseBasicParsing -TimeoutSec 5 -Headers @{Authorization="Bearer $tok"}
        Write-Host "  $p -> $($r.StatusCode)"
    } catch { Write-Host "  $p -> ERROR ($($_.Exception.Message))" }
}

Write-Host ''
Write-Host '=== Anthropic credit probe (does Anthropic return 401/402 vs working?) ===' -ForegroundColor Cyan
try {
    $body = '{"model":"claude-sonnet","messages":[{"role":"user","content":"hi"}],"max_tokens":20}'
    $r = Invoke-WebRequest 'http://127.0.0.1:8791/v1/chat/completions' -Method POST -Headers @{ "x-api-key"=$env:ANTHROPIC_API_KEY; "Content-Type"="application/json"; "anthropic-version"="2023-06-01" } -Body $body -UseBasicParsing -TimeoutSec 30
    Write-Host "  status: $($r.StatusCode)"
    Write-Host "  body:   $($r.Content.Substring(0, [Math]::Min(300, $r.Content.Length)))"
} catch {
    Write-Host "  status: $($_.Exception.Response.StatusCode.value__)" -ForegroundColor Yellow
    if ($_.Exception.Response) { (New-Object System.IO.StreamReader $_.Exception.Response.GetResponseStream()).ReadToEnd() }
}

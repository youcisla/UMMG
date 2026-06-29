# refresh_oauth.ps1 — trade the OAuth refreshToken in
# C:\Users\Y.CHEHBOUB\.claude\.credentials.json for a fresh access token,
# write it into .env so the gateway keeps working without re-login.
#
# Schedule with Task Scheduler -> At logon or every 6h.
#
# Requires: internet access to console.anthropic.com / auth.anthropic.com.

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$CredPath = Join-Path $env:USERPROFILE '.claude\.credentials.json'
$EnvPath  = 'C:\Tools\gateway\.env'

if (-not (Test-Path $CredPath)) {
    Write-Host "ERROR: $CredPath missing. Log into claude.ai and use Claude Code once first." -ForegroundColor Red
    exit 1
}

$cred = Get-Content $CredPath -Raw | ConvertFrom-Json
if (-not $cred.claudeAiOauth -or -not $cred.claudeAiOauth.refreshToken) {
    Write-Host "ERROR: no claudeAiOauth.refreshToken in $CredPath" -ForegroundColor Red
    exit 1
}

$rt  = $cred.claudeAiOauth.refreshToken
$old = $cred.claudeAiOauth.accessToken
$subs = $cred.claudeAiOauth.subscriptionType
$minRemaining = 0
try {
    $epochMs = [long]$cred.claudeAiOauth.expiresAt
    $dt = (Get-Date '1970-01-01').AddMilliseconds($epochMs)
    $minRemaining = [int](($dt - (Get-Date)).TotalMinutes)
} catch {}

Write-Host "current token: expires in $minRemaining min  subscription=$subs" -ForegroundColor Gray

# Try Anthropic's OAuth refresh endpoint. The Claude Code CLI uses the same
# flow; the exact endpoint isn't fully documented but is reachable via the
# Anthropic console OAuth client used by the desktop app.
# Endpoint guess (consistent with Anthropic's public OAuth behavior):
$refreshCandidates = @(
    'https://console.anthropic.com/api/oauth/token',
    'https://auth.anthropic.com/oauth/token',
    'https://api.anthropic.com/oauth/token'
)

$newToken = $null
$newRefresh = $null
$newExpiresMs = $null

foreach ($url in $refreshCandidates) {
    try {
        Write-Host "trying $url ..." -ForegroundColor Gray
        $body = @{
            grant_type    = 'refresh_token'
            refresh_token = $rt
            client_id     = 'https://claude.ai/oauth/claude-code-client-metadata'
        } | ConvertTo-Json
        $r = Invoke-WebRequest $url -Method POST -Headers @{
            'Content-Type' = 'application/json'
            'Accept'       = 'application/json'
        } -Body $body -UseBasicParsing -TimeoutSec 20
        if ($r.StatusCode -eq 200) {
            $obj = $r.Content | ConvertFrom-Json
            $newToken       = $obj.access_token
            $newRefresh     = if ($obj.refresh_token) { $obj.refresh_token } else { $rt }
            $newExpiresMs   = (Get-Date).AddSeconds([int]$obj.expires_in).Subtract((Get-Date '1970-01-01')).Ticks / 10000
            break
        }
    } catch {
        Write-Host "  $($url): $($_.Exception.Response.StatusCode.value__)" -ForegroundColor DarkGray
        continue
    }
}

if (-not $newToken) {
    Write-Host "ERROR: refresh failed via all candidate endpoints. The OAuth flow probably requires a re-login." -ForegroundColor Red
    Write-Host "Run:  claude auth login" -ForegroundColor Yellow
    exit 2
}

# Update .claude/.credentials.json
$cred.claudeAiOauth.accessToken  = $newToken
$cred.claudeAiOauth.refreshToken = $newRefresh
$cred.claudeAiOauth.expiresAt    = [long]$newExpiresMs
$cred | ConvertTo-Json -Depth 10 | Set-Content -Path $CredPath -Encoding UTF8

# Update .env: replace the ANTHROPIC_API_KEY line
$lines = Get-Content $EnvPath
$out = New-Object System.Collections.Generic.List[string]
foreach ($l in $lines) {
    if ($l -match '^ANTHROPIC_API_KEY=') {
        $out.Add("ANTHROPIC_API_KEY=$newToken")
    } else {
        $out.Add($l)
    }
}
Set-Content -Path $EnvPath -Value $out -Encoding ASCII

$minutes = [int]((Get-Date '1970-01-01').AddMilliseconds($newExpiresMs) - (Get-Date)).TotalMinutes
Write-Host "OK  new token written to .env (expires in $minutes min)" -ForegroundColor Green
Write-Host "Restart the gateway with: powershell -File 'C:\Tools\gateway\stop.ps1'; powershell -File 'C:\Tools\gateway\start.ps1'" -ForegroundColor Yellow

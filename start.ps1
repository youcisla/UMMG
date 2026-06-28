# UMMG — start the gateway and the two headroom bridges.
#
# Uses cmd /c start /B to detach children because Start-Process with
# -RedirectStandardOutput/-RedirectStandardError hangs on Windows
# PowerShell 5.1 when the child writes to those streams (headroom's
# startup banner is enough to wedge it).

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = $ScriptDir
$EnvPath = Join-Path $ProjectDir '.env'

if (-not (Test-Path $EnvPath)) {
    Write-Host "ERROR: .env not found at $EnvPath" -ForegroundColor Red
    Write-Host "Copy .env.example to .env and fill in the values." -ForegroundColor Yellow
    exit 1
}

Write-Host "Loading .env..." -ForegroundColor Cyan
Get-Content $EnvPath | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
    $name, $value = $_ -split '=', 2
    Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim().Trim('"').Trim("'")
}

foreach ($v in @('GATEWAY_BEARER_TOKEN','ANTHROPIC_API_KEY','MINIMAX_API_KEY')) {
    if (-not (Test-Path env:$v) -or (Get-Item env:$v).Value -like 'change-me*' -or (Get-Item env:$v).Value -like 'sk-...') {
        Write-Host "ERROR: $v is missing or still set to placeholder in .env" -ForegroundColor Red
        exit 1
    }
}

# Locate headroom and python.
$HeadroomExe = (Get-Command headroom.exe -ErrorAction SilentlyContinue).Source
if (-not $HeadroomExe) {
    $Candidate = Join-Path $env:USERPROFILE '.local\bin\headroom.exe'
    if (Test-Path $Candidate) { $HeadroomExe = $Candidate }
}
if (-not $HeadroomExe) {
    Write-Host "ERROR: headroom.exe not found on PATH or at $Candidate" -ForegroundColor Red
    exit 1
}
Write-Host "headroom: $HeadroomExe" -ForegroundColor DarkGray

$PythonExe = $null
foreach ($c in @(
    (Join-Path $env:USERPROFILE 'AppData\Local\Python\bin\python.exe'),
    (Join-Path $env:USERPROFILE '.local\bin\python3.11.exe'),
    (Join-Path $env:USERPROFILE 'AppData\Local\Python\bin\python3.14.exe')
)) {
    if (Test-Path $c) { $PythonExe = $c; break }
}
if (-not $PythonExe) { $PythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $PythonExe) { $PythonExe = 'python' }
Write-Host "python:   $PythonExe" -ForegroundColor DarkGray

function Kill-Port([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $pid_ = $_.OwningProcess
        if ($pid_) {
            if (-not $Force) {
                Write-Host "Port ${Port} is held by PID ${pid_}. Killing." -ForegroundColor Yellow
            }
            try { Stop-Process -Id $pid_ -Force -ErrorAction Stop } catch {}
        }
    }
    Start-Sleep -Milliseconds 300
}

Write-Host "Freeing ports 8787 / 8791 / 8792..." -ForegroundColor Cyan
Kill-Port 8787
Kill-Port 8791
Kill-Port 8792

# Launch a long-running process, fully detached from this PowerShell's
# job object and with stdout/stderr redirected to a log file via a
# tiny cmd shim. This:
#   1. Breaks the Job-Object parent/child relationship so the child
#      survives this script's exit (critical for Startup-folder use).
#   2. Avoids Start-Process -RedirectStandard* which hangs on PS 5.1.
#   3. Captures output to disk for post-mortem diagnostics.
#   4. Sets the working directory inside the shim so 'python main.py'
#      resolves to <ProjectDir>\main.py even when this script was
#      launched from a Startup-folder shortcut with no CWD.
function Launch-Detached([string]$Exe, [string[]]$ArgList, [string]$OutLog, [string]$ErrLog, [string]$WorkDir = '') {
    $shim = [System.IO.Path]::GetTempFileName() + '.cmd'
    $quotedExe = '"' + $Exe + '"'
    $rest = ($ArgList | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $body = "@echo off`r`n"
    if ($WorkDir -ne '') {
        $body += "cd /d ""$WorkDir""`r`n"
    }
    $body += "start """" /B $quotedExe $rest > ""$OutLog"" 2> ""$ErrLog""`r`n"
    [System.IO.File]::WriteAllText($shim, $body, [System.Text.Encoding]::ASCII)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'cmd.exe'
    $psi.Arguments = '/c "' + $shim + '"'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    Start-Sleep -Milliseconds 200
    Remove-Item $shim -Force -ErrorAction SilentlyContinue
    return $proc
}

Write-Host "Starting headroom (anthropic) on 8791..." -ForegroundColor Cyan
$env:ANTHROPIC_API_KEY = (Get-Item env:ANTHROPIC_API_KEY).Value
$hrAnthropic = Launch-Detached $HeadroomExe @(
    'proxy',
    '--port', '8791',
    '--backend', 'anthropic',
    '--no-memory-context',
    '--no-memory-tools'
) (Join-Path $ProjectDir 'logs\headroom-anthropic.out.log') `
  (Join-Path $ProjectDir 'logs\headroom-anthropic.err.log')
Write-Host "  headroom-anthropic shim PID = $($hrAnthropic.Id)" -ForegroundColor DarkGray

Write-Host "Starting headroom (openai/minimax) on 8792..." -ForegroundColor Cyan
$env:OPENAI_API_KEY = (Get-Item env:MINIMAX_API_KEY).Value
$env:OPENAI_TARGET_API_URL = 'https://api.minimax.io/v1'
$hrMinimax = Launch-Detached $HeadroomExe @(
    'proxy',
    '--port', '8792',
    '--backend', 'openai',
    '--no-memory-context',
    '--no-memory-tools'
) (Join-Path $ProjectDir 'logs\headroom-minimax.out.log') `
  (Join-Path $ProjectDir 'logs\headroom-minimax.err.log')
Write-Host "  headroom-minimax shim PID = $($hrMinimax.Id)" -ForegroundColor DarkGray

function Test-Port([string]$Target, [int]$Port, [int]$TimeoutSec = 15) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $tcp = New-Object System.Net.Sockets.TcpClient
        try {
            $iar = $tcp.BeginConnect($Target, $Port, $null, $null)
            $ok = $iar.AsyncWaitHandle.WaitOne(500, $false)
            if ($ok) {
                $tcp.EndConnect($iar)
                $tcp.Close()
                return $true
            }
        } catch {}
        finally { try { $tcp.Close() } catch {} }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

Write-Host "Probing headroom 8791..." -ForegroundColor Cyan
if (-not (Test-Port '127.0.0.1' 8791 30)) {
    Write-Host "ERROR: headroom-anthropic failed to bind 8791" -ForegroundColor Red
    Write-Host "  See logs\headroom-anthropic.err.log" -ForegroundColor Yellow
    Write-Host "  Continuing to try other components anyway." -ForegroundColor Yellow
} else {
    Write-Host "  8791 OK" -ForegroundColor Green
}

Write-Host "Probing headroom 8792..." -ForegroundColor Cyan
if (-not (Test-Port '127.0.0.1' 8792 30)) {
    Write-Host "ERROR: headroom-minimax failed to bind 8792" -ForegroundColor Red
    Write-Host "  See logs\headroom-minimax.err.log" -ForegroundColor Yellow
    Write-Host "  Continuing to try other components anyway." -ForegroundColor Yellow
} else {
    Write-Host "  8792 OK" -ForegroundColor Green
}

# Capture all output (including any Write-Host failures at boot when
# no console is attached) to a transcript file. This makes it
# possible to diagnose boot-time failures after the fact.
$transcript = Join-Path $ProjectDir 'logs\start.transcript.log'
try { Start-Transcript -Path $transcript -Append -ErrorAction SilentlyContinue } catch {}

try {
if (Test-Port '127.0.0.1' 11434 3) {
    Write-Host "Ollama: detected on 11434 (local models available)" -ForegroundColor Green
} else {
    Write-Host "Ollama: not detected on 11434 (local-* models will return 503 until you start Ollama)" -ForegroundColor Yellow
}

Write-Host "Starting gateway on 8787..." -ForegroundColor Cyan
Write-Host "  python: $PythonExe" -ForegroundColor DarkGray
Write-Host "  args:   main.py" -ForegroundColor DarkGray
Write-Host "  cwd:    $ProjectDir" -ForegroundColor DarkGray
Write-Host "  out:    $(Join-Path $ProjectDir 'logs\gateway.out.log')" -ForegroundColor DarkGray
Write-Host "  err:    $(Join-Path $ProjectDir 'logs\gateway.err.log')" -ForegroundColor DarkGray
try {
    $gwProc = Launch-Detached $PythonExe @('main.py') `
        (Join-Path $ProjectDir 'logs\gateway.out.log') `
        (Join-Path $ProjectDir 'logs\gateway.err.log') `
        $ProjectDir
    Write-Host "  gateway shim PID = $($gwProc.Id)" -ForegroundColor DarkGray
} catch {
    Write-Host "  ERROR launching gateway: $_" -ForegroundColor Red
}

Write-Host "Probing gateway 8787 (may take up to 90s on first boot while sentence-transformers downloads)..." -ForegroundColor Cyan
if (-not (Test-Port '127.0.0.1' 8787 90)) {
    Write-Host "WARNING: gateway not reachable on 8787 within 90s." -ForegroundColor Yellow
    Write-Host "  The gateway process is still running (PID $($gwProc.Id)) and will keep trying to boot." -ForegroundColor Yellow
    Write-Host "  Check logs\gateway.err.log for details." -ForegroundColor Yellow
    Write-Host "  Once it finishes loading the embedding model, http://127.0.0.1:8787/health will respond." -ForegroundColor Yellow
} else {
    Write-Host "  8787 OK" -ForegroundColor Green
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  UMMG is running on http://127.0.0.1:8787" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Health:    curl http://127.0.0.1:8787/health" -ForegroundColor Cyan
Write-Host "Models:    curl http://127.0.0.1:8787/v1/models" -ForegroundColor Cyan
$TokShort = (Get-Item env:GATEWAY_BEARER_TOKEN).Value.Substring(0, [Math]::Min(8, (Get-Item env:GATEWAY_BEARER_TOKEN).Value.Length)) + '...'
Write-Host "Chat:"
Write-Host "  curl -H `"Authorization: Bearer <GATEWAY_BEARER_TOKEN>`" -H `"Content-Type: application/json`" ``" -ForegroundColor Cyan
Write-Host "       -d '{\"model\":\"minimax-m3\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}' ``" -ForegroundColor Cyan
Write-Host "       http://127.0.0.1:8787/v1/chat/completions" -ForegroundColor Cyan
Write-Host ""
Write-Host "Stop with: .\stop.ps1" -ForegroundColor Cyan

} catch {
    Write-Host "FATAL: unhandled error in start.ps1: $_" -ForegroundColor Red
    Write-Host "  See $transcript for details." -ForegroundColor Yellow
} finally {
    try { Stop-Transcript -ErrorAction SilentlyContinue } catch {}
}
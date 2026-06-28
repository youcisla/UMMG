# UMMG shutdown. Kills the gateway + the two headroom bridges.
# Memory store (data/) is preserved across restarts.

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = $ScriptDir
$PidFile = Join-Path $ProjectDir 'logs\.pids.json'

function Kill-Port([int]$Port) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $pid_ = $c.OwningProcess
        if ($pid_) {
            Write-Host "Killing PID ${pid_} on port ${Port}..." -ForegroundColor Yellow
            try { Stop-Process -Id $pid_ -Force -ErrorAction Stop } catch {}
        }
    }
}

if (Test-Path $PidFile) {
    try {
        $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
        foreach ($name in @('gateway','headroom_anthropic','headroom_minimax')) {
            $pid_ = $pids.$name
            if ($pid_) {
                Write-Host "Stopping $name (PID $pid_)..." -ForegroundColor Yellow
                try { Stop-Process -Id $pid_ -Force -ErrorAction Stop } catch {}
            }
        }
    } catch {
        Write-Host "PID file unreadable; falling back to port-based kill." -ForegroundColor Yellow
    }
}

Start-Sleep -Milliseconds 500

foreach ($p in 8787, 8791, 8792) {
    Kill-Port $p
}

Write-Host "UMMG stopped. Memory store (data/) preserved." -ForegroundColor Green
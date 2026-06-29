Write-Host '=== Is anything listening on 8787? ===' -ForegroundColor Cyan
Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue | Format-Table -AutoSize
Write-Host '(empty = nothing listening = auto-start did not fire)' -ForegroundColor Gray

Write-Host ''
Write-Host '=== Auto-start shortcut still exists? ===' -ForegroundColor Cyan
$link = Join-Path ([Environment]::GetFolderPath('Startup')) 'UMMG-Gateway.lnk'
if (Test-Path $link) {
    Write-Host "  YES: $link" -ForegroundColor Green
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($link)
    $sc | Select-Object TargetPath, Arguments, WorkingDirectory, WindowStyle | Format-List
} else {
    Write-Host '  NO - shortcut missing' -ForegroundColor Red
}

Write-Host ''
Write-Host '=== When did the gateway last actually run? ===' -ForegroundColor Cyan
$out = 'C:\Tools\gateway\logs\gateway.out.log'
$err = 'C:\Tools\gateway\logs\gateway.err.log'
if (Test-Path $out) { Write-Host "  gateway.out.log lastWriteTime: $((Get-Item $out).LastWriteTime)" }
if (Test-Path $err) { Write-Host "  gateway.err.log lastWriteTime: $((Get-Item $err).LastWriteTime)" }

Write-Host ''
Write-Host '=== Any related Python/headroom processes? ===' -ForegroundColor Cyan
Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessName -match 'python|headroom|powershell'
} | Select-Object Id, ProcessName, StartTime, @{N='Cmd';E={(Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine.Substring(0, [Math]::Min(150, (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine.Length))}} | Format-Table -AutoSize -Wrap

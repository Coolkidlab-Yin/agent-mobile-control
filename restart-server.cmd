@echo off
rem Restart the claude-chat server: kill only the pythonw.exe whose command line contains
rem this repo's folder (never "whoever is listening on the port"), start it again via
rem start-server.cmd, then print the new process start time as proof.
rem A health-check 200 alone is NOT proof of a restart - a stale process answers 200 too.
setlocal
set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$here = $env:HERE.TrimEnd('\');" ^
  "$ps = Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($here, [StringComparison]::OrdinalIgnoreCase) -ge 0 };" ^
  "foreach ($p in $ps) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Start-Sleep 1; Start-Process -WindowStyle Hidden -FilePath (Join-Path $here 'start-server.cmd');" ^
  "$ok = $false; for ($i = 0; $i -lt 20; $i++) { Start-Sleep 1; try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:8899/api/health; if ($r.StatusCode -eq 200) { $ok = $true; break } } catch {} };" ^
  "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($here, [StringComparison]::OrdinalIgnoreCase) -ge 0 } | ForEach-Object { 'pid=' + $_.ProcessId + ' started=' + $_.CreationDate };" ^
  "if ($ok) { 'health OK - check that the started= time above is NOW' } else { 'health FAILED' }"
pause

# Save the 1-year Claude Code token (from `claude setup-token`) as a DPAPI file.
# ASCII only (no BOM needed). Only this Windows user on this machine can decrypt it.
# Read by: claude_chat/runner.py (used when /login is dead) and any scheduled `claude -p`.
# Rerun this after a new setup-token (yearly) or after moving to a new PC.
#
# Using it from your own scheduled task (the task must run as Interactive logon,
# "run only when user is logged on", or DPAPI cannot decrypt it):
#   $f = Join-Path $env:USERPROFILE ".claude\secrets\claude-oauth-token.dpapi"
#   $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR((Get-Content $f -Raw | ConvertTo-SecureString))
#   $env:CLAUDE_CODE_OAUTH_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)
#   [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)
#   claude -p "..."

$dir = Join-Path $env:USERPROFILE ".claude\secrets"
$file = Join-Path $dir "claude-oauth-token.dpapi"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$sec = Read-Host -AsSecureString "Paste the token from 'claude setup-token' (input hidden), then Enter"
if ($sec.Length -lt 20) {
  Write-Host "Too short ($($sec.Length) chars) - nothing saved." -ForegroundColor Red
  exit 1
}
$sec | ConvertFrom-SecureString | Set-Content -Path $file -NoNewline
Write-Host "Saved ($($sec.Length) chars) -> $file" -ForegroundColor Green

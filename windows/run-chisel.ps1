# Chisel client for the Windows box behind FortiGate.
# Opens a reverse SOCKS5 on the hub; traffic sent to the hub's 1080
# exits here into the local LAN. Keep this window open.
# Usage: powershell -ExecutionPolicy Bypass -File run-chisel.ps1
# Auth via env var so the secret never lands in a file:
#   $env:CHISEL_AUTH = 'spider:...'; .\run-chisel.ps1
$ErrorActionPreference = "Stop"
$HUB = "https://spider-chisel.onrender.com"
$AUTH = if ($env:CHISEL_AUTH) { $env:CHISEL_AUTH } else { "spider:CHANGE_ME" }
$exe = Join-Path $PSScriptRoot "chisel.exe"
if (-not (Test-Path $exe)) {
  $zip = Join-Path $env:TEMP "chisel.zip"
  Invoke-WebRequest -Uri "https://github.com/jpillora/chisel/releases/download/v1.12.0/chisel_1.12.0_windows_amd64.zip" -OutFile $zip
  Expand-Archive $zip -DestinationPath $PSScriptRoot -Force
}
while ($true) {
  Write-Host "Connecting to $HUB ..."
  & $exe client --auth $AUTH --keepalive 25s $HUB R:socks
  Write-Host "Disconnected (exit $LASTEXITCODE). Retrying in 10s ..."
  Start-Sleep -Seconds 10
}

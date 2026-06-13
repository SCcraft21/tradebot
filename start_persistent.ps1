# Detached persistent launcher for the Trading Bot
# This script spawns run_bot_daemon.ps1 in a hidden window that survives terminal closure.

$ScriptPath = Join-Path (Get-Location).Path "run_bot_daemon.ps1"

# Execute the script directly using powershell.exe with -WindowStyle Hidden
Start-Process "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -Mode both" -WorkingDirectory (Get-Location).Path -WindowStyle Hidden

Write-Output "[LAUNCHER] Bot daemon has been spawned persistently in a hidden process group."

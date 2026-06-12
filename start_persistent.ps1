# Detached persistent launcher for the Trading Bot
# This script spawns run_bot_daemon.ps1 in a hidden window that survives terminal closure.

$ScriptPath = Join-Path (Get-Location).Path "run_bot_daemon.ps1"
$VbsPath = Join-Path (Get-Location).Path "launch_hidden.vbs"

# Write a tiny VBScript that can run commands without displaying a cmd/powershell console window
$VbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & WScript.Arguments(0) & """ -Mode both", 0, false
"@

[System.IO.File]::WriteAllText($VbsPath, $VbsContent)

# Execute the VBScript using wscript.exe to launch the daemon runner detached and hidden
Start-Process "wscript.exe" -ArgumentList "`"$VbsPath`" `"$ScriptPath`"" -NoNewWindow

Write-Output "[LAUNCHER] Bot daemon has been spawned persistently in a hidden process group via wscript.exe."

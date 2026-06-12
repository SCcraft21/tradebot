# Trading Bot 24/7 Windows Daemon Monitor
# Usage: powershell.exe -ExecutionPolicy Bypass -File run_bot_daemon.ps1 -Mode crypto

param (
    [string]$Mode = "crypto"
)

if ($Mode -ne "crypto" -and $Mode -ne "stocks" -and $Mode -ne "both") {
    Write-Output "[DAEMON] CRITICAL: Invalid Mode '$Mode'. Only 'crypto', 'stocks', and 'both' are allowed."
    exit 1
}

# Set working directory to the directory of this script
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($ScriptDir) {
    Set-Location $ScriptDir
}

$LogFile = "daemon_runner.log"

# Function to write log entries
function Write-DaemonLog ($Message) {
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $LogLine = "[$Timestamp] [DAEMON] $Message"
    Write-Output $LogLine
    Add-Content -Path $LogFile -Value $LogLine
}

# Function to extract telegram settings from config.yaml using regex
function Get-TelegramConfig {
    if (-not (Test-Path "config.yaml")) {
        return $null
    }
    $yaml = Get-Content -Raw "config.yaml"
    
    # Extract token
    $token = ""
    if ($yaml -match 'telegram_token:\s*"([^"]*)"') { $token = $Matches[1] }
    elseif ($yaml -match "telegram_token:\s*'([^']*)'") { $token = $Matches[1] }
    elseif ($yaml -match 'telegram_token:\s*([^\r\n]*)') { $token = $Matches[1].Trim().Trim('"').Trim("'") }
    
    # Extract chat_id
    $chatId = ""
    if ($yaml -match 'telegram_chat_id:\s*"([^"]*)"') { $chatId = $Matches[1] }
    elseif ($yaml -match "telegram_chat_id:\s*'([^']*)'") { $chatId = $Matches[1] }
    elseif ($yaml -match 'telegram_chat_id:\s*([^\r\n]*)') { $chatId = $Matches[1].Trim().Trim('"').Trim("'") }
    
    return [PSCustomObject]@{
        Token = $token
        ChatId = $chatId
    }
}

# Function to send Telegram message directly from the daemon
function Send-TelegramAlert ($Message) {
    $tg = Get-TelegramConfig
    if ($tg -and $tg.Token -and $tg.ChatId) {
        try {
            $url = "https://api.telegram.org/bot$($tg.Token)/sendMessage"
            $body = @{
                chat_id = $tg.ChatId
                text = $Message
            } | ConvertTo-Json
            $null = Invoke-RestMethod -Uri $url -Method Post -ContentType "application/json" -Body $body -TimeoutSec 5
        } catch {
            Write-DaemonLog "Failed to send Telegram alert: $($_)"
        }
    }
}

Write-DaemonLog "=========================================="
Write-DaemonLog "Starting Trading Bot Daemon in mode: $Mode"
Write-DaemonLog "=========================================="

$KeepRunning = $true

while ($KeepRunning) {
    # Check if virtual environment exists
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Write-DaemonLog "CRITICAL: Python virtual environment (.venv) not found. Rebuild environment first."
        Send-TelegramAlert "[BOT DAEMON] Critical error: Virtual environment (.venv) not found!"
        break
    }

    Write-DaemonLog "Launching trading bot: python.exe main.py --mode $Mode trade"
    
    try {
        # Launch bot and wait for it to complete
        $Process = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "main.py --mode $Mode trade" -NoNewWindow -PassThru -Wait
        
        $ExitCode = $Process.ExitCode
        Write-DaemonLog "Trading bot process exited with code: $ExitCode"
        
        if ($ExitCode -eq 0) {
            Write-DaemonLog "Bot exited normally. Stopping daemon."
            Send-TelegramAlert "[BOT DAEMON] INFO: Trading bot has shut down gracefully."
            $KeepRunning = $false
        } else {
            Write-DaemonLog "Bot crashed! Restarting in 5 seconds..."
            Send-TelegramAlert "[BOT DAEMON] WARNING: Trading bot crashed (Exit Code: $ExitCode)! Restarting in 5 seconds..."
            Start-Sleep -Seconds 5
        }
    } catch {
        Write-DaemonLog "An exception occurred while running the process: $($_)"
        Send-TelegramAlert "[BOT DAEMON] WARNING: Daemon exception occurred: $($_). Restarting in 10 seconds..."
        Start-Sleep -Seconds 10
    }
}

Write-DaemonLog "Daemon stopped."

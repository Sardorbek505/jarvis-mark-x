# install_autostart.ps1 - Register JARVIS PC Server as a Windows Startup Task
# Run as Administrator: powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1

param(
    [string]$JarvisDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "python"
)

$TaskName   = "JARVIS PC Server"
$LogFile    = Join-Path $JarvisDir "jarvis_pc_server.log"
$ScriptPath = Join-Path $JarvisDir "scripts\start_pc.bat"

Write-Host "Installing JARVIS PC Server as startup task..."
Write-Host "  Project dir : $JarvisDir"
Write-Host "  Python      : $PythonExe"
Write-Host "  Log file    : $LogFile"
Write-Host ""

# Remove old task if exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Run the .bat via a SINGLE quoted token after /c. The old form appended
# `>> log 2>&1`, which made cmd's quote-stripping mangle the command (it failed
# before the bat ran -> empty log). The bat now self-logs, so no redirection here.
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$ScriptPath`"" `
    -WorkingDirectory $JarvisDir

# AtLogon fires before the network is up; delay 20s so WiFi/Ethernet is ready.
# (The bat also waits for internet, so this is belt-and-suspenders.)
$trigger = New-ScheduledTaskTrigger -AtLogon
$trigger.Delay = "PT20S"

# NOTE: switch parameters must be bare (-StartWhenAvailable), NOT "-X $true"
# (a value after a switch is parsed as a positional arg -> binding error).
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

try {
    Register-ScheduledTask `
        -TaskName  $TaskName `
        -Action    $action `
        -Trigger   $trigger `
        -Settings  $settings `
        -Principal $principal `
        -Force -ErrorAction Stop | Out-Null
} catch {
    Write-Host "[ERROR] Failed to register task: $($_.Exception.Message)"
    exit 1
}

Write-Host "[OK] Task '$TaskName' registered - starts at every Windows login."

# Start it right now so you don't need to reboot.
try {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "[OK] Started now. In a few seconds you'll get 'PC online' in Telegram."
} catch {
    Write-Host "[WARN] Registered, but did not start: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$TaskName'"

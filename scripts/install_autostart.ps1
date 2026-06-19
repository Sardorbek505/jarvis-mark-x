# install_autostart.ps1 - Register JARVIS PC Server as a Windows Startup Task
# Run as Administrator: powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1

param(
    [string]$JarvisDir = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "python"
)

$TaskName   = "JARVIS PC Server"
$LogFile    = Join-Path $JarvisDir "jarvis_pc_server.log"
$ScriptPath = Join-Path $JarvisDir "scripts\start_pc.bat"
$VbsPath    = Join-Path $JarvisDir "scripts\start_pc_hidden.vbs"

Write-Host "Installing JARVIS PC Server as startup task..."
Write-Host "  Project dir : $JarvisDir"
Write-Host "  Python      : $PythonExe"
Write-Host "  Log file    : $LogFile"
Write-Host ""

# Stop the task + kill any stale instance (visible cmd window + its python) so
# the new hidden launcher fully takes over. Runs elevated, so the kill works.
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='cmd.exe' OR Name='python.exe' OR Name='wscript.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*start_pc*" -or $_.CommandLine -like "*pc_server*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Remove old task if exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Launch fully HIDDEN via a VBS shim (wscript shows no window). The .bat itself
# self-logs to $LogFile, so nothing is lost despite there being no console.
# (Earlier the task used `cmd /c "bat" >> log 2>&1`; the multi-quote redirection
# got mangled by Task Scheduler and the bat never ran -> empty log.)
$action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "`"$VbsPath`"" `
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

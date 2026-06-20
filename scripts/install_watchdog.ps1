# Register the JARVIS PC Watchdog - keeps pc_server alive (boot + every minute).
# RunLevel Limited => registers WITHOUT admin (pc_server needs no elevation).
# Run: powershell -ExecutionPolicy Bypass -File scripts\install_watchdog.ps1
param([string]$JarvisDir = (Split-Path -Parent $PSScriptRoot))

$TaskName = "JARVIS PC Watchdog"
$Script   = Join-Path $JarvisDir "scripts\pc_watchdog.ps1"

Write-Host "Installing watchdog task..."
Write-Host "  Script: $Script"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`""

# Two triggers: one at logon (for boot), and one that starts NOW and repeats
# every minute forever. The repeating-now trigger is what makes it active
# immediately (an AtLogon+repetition only repeats AFTER a fresh logon).
$trigBoot = New-ScheduledTaskTrigger -AtLogon
$trigBoot.Delay = "PT20S"
$trigLoop = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$trigger = @($trigBoot, $trigLoop)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
    Write-Host "[OK] Watchdog registered - checks every minute."
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    exit 1
}

try {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "[OK] Watchdog started now."
} catch {
    Write-Host "[WARN] Registered but did not start now: $($_.Exception.Message)"
}

# install_autostart.ps1 — Register JARVIS PC Server as a Windows Startup Task
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

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$ScriptPath`" >> `"$LogFile`" 2>&1" `
    -WorkingDirectory $JarvisDir

$trigger = New-ScheduledTaskTrigger -AtLogon

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable $true `
    -RunOnlyIfNetworkAvailable $true

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "[OK] Task registered: '$TaskName'"
Write-Host "     Starts automatically at every login."
Write-Host ""
Write-Host "To start now:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "To remove:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName'"

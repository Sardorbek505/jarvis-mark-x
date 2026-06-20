# JARVIS PC watchdog - ensures pc_server is always running.
# Run every minute by the "JARVIS PC Watchdog" scheduled task. ASCII-only output
# (Windows PowerShell 5.1 reads .ps1 as ANSI; Cyrillic breaks the parser).
$proj = Split-Path -Parent $PSScriptRoot
$log  = Join-Path $proj "jarvis_watchdog.log"

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*telegram_bot.pc_server*" }

if ($running) { exit 0 }  # already alive - nothing to do

try {
    Start-Process -FilePath "python" `
        -ArgumentList "-m", "telegram_bot.pc_server" `
        -WorkingDirectory $proj -WindowStyle Hidden
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [watchdog] pc_server was down -> relaunched" |
        Out-File -FilePath $log -Append -Encoding utf8
} catch {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [watchdog] relaunch FAILED: $($_.Exception.Message)" |
        Out-File -FilePath $log -Append -Encoding utf8
}

# JARVIS PC watchdog - ensures pc_server is always running.
# Run every minute by the "JARVIS PC Watchdog" scheduled task. ASCII-only output
# (Windows PowerShell 5.1 reads .ps1 as ANSI; Cyrillic breaks the parser).
$proj = Split-Path -Parent $PSScriptRoot
$log  = Join-Path $proj "jarvis_watchdog.log"

# Liveness = "is the machine lock held?", i.e. is anything listening on the port
# pc_server binds at startup. The old check matched Win32_Process CommandLine,
# which comes back EMPTY for a client launched by the elevated autostart task:
# a non-elevated query cannot read an elevated process's command line. So this
# watchdog saw "down" every single minute and spawned another client, and the
# duplicates then fought over the bridge - one of them hung for 7 minutes on
# 17.08.2026 without writing a single log line.
# Get-NetTCPConnection is passive: no connection is opened, so repeated probes
# cannot pile up in the listener's accept queue.
$lockPort = 47821
$held = Get-NetTCPConnection -LocalPort $lockPort -State Listen -ErrorAction SilentlyContinue

if ($held) { exit 0 }  # already alive - nothing to do

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

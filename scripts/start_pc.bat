@echo off
REM JARVIS PC Server launcher — used by the autostart task and manually.
REM Self-logs (so we never depend on the Task Scheduler redirection, which
REM mangles quotes), waits for the network at logon, then runs pc_server in a
REM restart loop. NOTE: `timeout` fails in non-interactive task sessions, so we
REM sleep with `ping 127.0.0.1` (the classic, console-free delay trick).
REM NOTE: pc_server.py writes jarvis_pc_server.log itself (with rotation), so we
REM must NOT redirect into that file — an open handle blocks rotation on Windows.
REM This launcher keeps its own small boot log instead.
title JARVIS PC Server
cd /d "%~dp0\.."
set "LOG=%~dp0..\logs\pc_boot.log"
if not exist "%~dp0..\logs" mkdir "%~dp0..\logs"

REM --- Keep the boot log bounded (roll over past ~1 MB) ---
for %%F in ("%LOG%") do if %%~zF GTR 1048576 move /y "%LOG%" "%LOG%.1" >nul

>>"%LOG%" echo.
>>"%LOG%" echo =========================================
>>"%LOG%" echo  JARVIS PC Server start %DATE% %TIME%
>>"%LOG%" echo =========================================

REM --- Wait for internet (logon trigger fires before WiFi/Ethernet is up) ---
set /a tries=0
:netwait
ping -n 1 8.8.8.8 >nul 2>&1
if %errorlevel%==0 goto netok
set /a tries+=1
if %tries% GEQ 40 goto netok
ping -n 3 127.0.0.1 >nul
goto netwait
:netok
>>"%LOG%" echo [net] ready after %tries% checks

REM --- Resolve python (PATH can differ in the task session) ---
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"

:loop
>>"%LOG%" echo [run] %DATE% %TIME% launching pc_server...
%PY% -m telegram_bot.pc_server >>"%LOG%" 2>&1
REM Code 3 = another client already holds the machine lock. That means a second
REM launcher chain is alive (the task started one while this loop was running).
REM Keep looping and we respawn every 5s forever, spamming the log; the extra
REM loop must retire instead — one launcher, one client.
if %errorlevel%==3 (
  >>"%LOG%" echo [=] another client is already running - this launcher exits.
  exit /b 0
)
>>"%LOG%" echo [!] pc_server stopped (code %errorlevel%), restart in 5s...
ping -n 6 127.0.0.1 >nul
goto loop

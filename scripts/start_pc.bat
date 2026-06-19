@echo off
REM JARVIS PC Server launcher — used by the autostart task and manually.
REM Self-logs (so we never depend on the Task Scheduler redirection, which
REM mangles quotes), waits for the network at logon, then runs pc_server in a
REM restart loop. NOTE: `timeout` fails in non-interactive task sessions, so we
REM sleep with `ping 127.0.0.1` (the classic, console-free delay trick).
title JARVIS PC Server
cd /d "%~dp0\.."
set "LOG=%~dp0..\jarvis_pc_server.log"

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
>>"%LOG%" echo [!] pc_server stopped (code %errorlevel%), restart in 5s...
ping -n 6 127.0.0.1 >nul
goto loop

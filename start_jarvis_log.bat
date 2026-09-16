@echo off
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
title JARVIS Mark X (log)
cd /d "%~dp0"
echo Log: logs\live.log  (crash: logs\crash.log). Ctrl+C - stop.
:loop
python -u main.py >> logs\live.log 2>&1
if %errorlevel% equ 0 goto end
echo [%time%] JARVIS crashed with code %errorlevel%, restarting in 3 s... >> logs\live.log
timeout /t 3 /nobreak >nul
goto loop
:end

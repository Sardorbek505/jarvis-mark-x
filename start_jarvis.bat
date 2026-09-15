@echo off
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
title JARVIS Mark X
cd /d "%~dp0"
echo ========================================================
echo   JARVIS Mark X — Запуск голосового ассистента
echo ========================================================
echo.
python main.py
if %errorlevel% neq 0 (
    echo.
    echo [ОШИБКА] Процесс завершился с кодом %errorlevel%.
    pause
)

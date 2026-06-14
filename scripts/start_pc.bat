@echo off
title JARVIS PC Server
cd /d "%~dp0\.."

echo =========================================
echo  JARVIS PC Server
echo =========================================
echo.
echo Подключение к Telegram боту...
echo Нажми Ctrl+C для остановки.
echo.

:loop
python -m telegram_bot.pc_server
echo.
echo [!] Сервер остановлен. Перезапуск через 5 секунд...
timeout /t 5 /nobreak > nul
goto loop

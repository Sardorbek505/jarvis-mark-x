@echo off
title JARVIS — All-in-One
cd /d "%~dp0\.."

echo =========================================
echo  JARVIS — Bot + PC Server
echo =========================================
echo.
echo Запускаю PC Server в отдельном окне...
start "JARVIS PC Server" cmd /c "scripts\start_pc.bat"

timeout /t 3 /nobreak > nul

echo Запускаю бота...
echo Не закрывай это окно. Ctrl+C для остановки.
echo.

:loop
python -m telegram_bot.bot
echo.
echo [!] Бот остановлен. Перезапуск через 5 секунд...
timeout /t 5 /nobreak > nul
goto loop

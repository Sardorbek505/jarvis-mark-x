@echo off
rem Обновить Telegram-бота на Hugging Face одной командой.
rem
rem   scripts\deploy_hf.bat            — выложить master
rem   scripts\deploy_hf.bat ВЕТКА      — выложить другую ветку
rem
rem Код берётся свежим с GitHub во временную папку: рабочая папка проекта
rem и config\api_keys.json не трогаются. На Hugging Face уходит только
rem текущее состояние, без истории (в истории лежал архив с ключами), и без
rem картинок — Space отклоняет бинарные файлы, а боту они не нужны.
chcp 65001 >nul
setlocal

set BRANCH=%~1
if "%BRANCH%"=="" set BRANCH=master
set SPACE=https://huggingface.co/spaces/atabekovch/jarvis-mark-x
set DIR=%TEMP%\jarvis-hf-deploy

echo.
echo Токен Hugging Face с правом Write: huggingface.co/settings/tokens
set /p T=Вставь токен и нажми Enter: 
if "%T%"=="" (
    echo Токен пустой — отмена.
    exit /b 1
)

if exist "%DIR%" rmdir /s /q "%DIR%"
echo Скачиваю ветку %BRANCH% с GitHub...
git clone -q --depth 1 -b %BRANCH% https://github.com/Sardorbek505/jarvis-mark-x.git "%DIR%" || goto :fail
pushd "%DIR%"

git checkout -q --orphan deploy
git rm -r -q --cached --ignore-unmatch app.ico assets face.png telegram_bot/miniapp/favicon.ico *.zip
git -c user.name=deploy -c user.email=deploy@localhost commit -q -m "deploy %BRANCH%" || goto :fail_pop

echo Отправляю на Hugging Face...
git push -q --force https://atabekovch:%T%@huggingface.co/spaces/atabekovch/jarvis-mark-x deploy:main || goto :fail_pop

popd
rmdir /s /q "%DIR%"
echo.
echo Готово. Space пересоберётся за 5-7 минут: %SPACE%
exit /b 0

:fail_pop
popd
:fail
echo.
echo Не получилось. Если ошибка про авторизацию — проверь токен (право Write).
exit /b 1

@echo off
cd /d "%~dp0"
echo ========================================
echo Saving changes to GitHub...
echo ========================================

REM Add all changes
echo Adding all changes...
git add .
if %errorlevel% neq 0 (
    echo [ERROR] Failed to add changes
    pause
    exit /b 1
)

REM Check if there are changes to commit
git diff --cached --quiet
if %errorlevel% equ 0 (
    echo [INFO] No changes to commit
    pause
    exit /b 0
)

REM Create commit with timestamp
echo Creating commit...
for /f "tokens=2 delims==" %%a in ('wmic os get localdatetime /value') do set datetime=%%a
set timestamp=%datetime:~0,8% %datetime:~8,6%
git commit -m "Auto-save %timestamp%"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create commit
    pause
    exit /b 1
)

REM Push to GitHub
echo Pushing to GitHub...
git push origin master
if %errorlevel% neq 0 (
    echo [ERROR] Failed to push to GitHub
    pause
    exit /b 1
)

echo ========================================
echo Successfully saved to GitHub!
echo ========================================
pause

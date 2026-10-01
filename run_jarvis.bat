@echo off
cd /d "%~dp0"
set MIC_DEVICE=1
start "" "%~dp0.venv\Scripts\python.exe" "%~dp0main.py"

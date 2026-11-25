@echo off
cd /d "%~dp0backend"
start "" ".\.venv\Scripts\pythonw.exe" -m app.desktop

@echo off
cd /d "%~dp0"
where python >nul 2>nul
if not errorlevel 1 (
    python gui.py
) else (
    py -3 gui.py
)
if errorlevel 1 pause

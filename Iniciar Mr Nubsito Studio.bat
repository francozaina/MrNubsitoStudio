@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw "Mr Nubsito Studio.py"
) else (
    start "" python "Mr Nubsito Studio.py"
)
exit

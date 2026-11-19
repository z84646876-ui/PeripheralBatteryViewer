@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "E:\Python\pythonw.exe" (
    start "" "E:\Python\pythonw.exe" tray_app.py
) else (
    start "" pythonw tray_app.py
)

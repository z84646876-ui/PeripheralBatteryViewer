@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "E:\Python\pythonw.exe" (
    start "" "E:\Python\pythonw.exe" my_devices.py
) else (
    start "" pythonw my_devices.py
)

@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".build_venv\Scripts\pyinstaller.exe" (
    py -3.12 -m venv .build_venv 2>nul || python -m venv .build_venv
    ".build_venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
)
".build_venv\Scripts\python.exe" tools\make_app_icon.py
".build_venv\Scripts\python.exe" tools\make_version_info.py ".build_tmp\file_version_info.txt"
".build_venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed ^
  --name "外设电量查看器" ^
  --icon "%~dp0assets\app_icon.ico" ^
  --add-data "%~dp0assets\app_icon.ico;assets" ^
  --distpath "%~dp0.." ^
  --workpath ".build_tmp" ^
  --specpath ".build_tmp" ^
  --version-file "%~dp0.build_tmp\file_version_info.txt" ^
  --hidden-import my_devices --hidden-import tray_app --hidden-import tray_service --hidden-import app ^
  --hidden-import watchdog --hidden-import neu_ui --hidden-import battery_history ^
  --hidden-import battery --hidden-import devices --hidden-import hid_win ^
  --hidden-import darkrim_proto --hidden-import bt_battery --hidden-import tray_common ^
  --hidden-import win_icon --hidden-import win_link --hidden-import win_shell --hidden-import winreg ^
  --hidden-import tkinter.filedialog --hidden-import tkinter.commondialog ^
  main.py

if errorlevel 1 (
    echo Build failed!
    exit /b 1
)

if defined PBV_SIGN_CERT (
    echo Signing output binary with PBV_SIGN_CERT...
    if defined PBV_SIGN_PASS (
        signtool sign /fd SHA256 /f "%PBV_SIGN_CERT%" /p "%PBV_SIGN_PASS%" /tr http://timestamp.digicert.com /td SHA256 "%~dp0..\外设电量查看器.exe"
    ) else (
        signtool sign /fd SHA256 /f "%PBV_SIGN_CERT%" /tr http://timestamp.digicert.com /td SHA256 "%~dp0..\外设电量查看器.exe"
    )
)

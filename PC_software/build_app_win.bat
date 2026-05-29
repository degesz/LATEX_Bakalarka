@echo off
REM Build script for Windows - run on a Windows machine with Python installed
setlocal

set APP_NAME=LCR_meter
set VENV_DIR=.venv_win

echo === Creating virtual environment ===
python -m venv %VENV_DIR%
call %VENV_DIR%\Scripts\activate.bat

echo === Installing dependencies ===
pip install pyinstaller pyqtgraph pyside6 pyserial

echo === Building executable ===
pyinstaller ^
    --onefile ^
    --windowed ^
    --name %APP_NAME% ^
    --add-data "icon;." ^
    --icon icon ^
    --collect-all PySide6 ^
    --clean ^
    --noconfirm ^
    LCR_meter_app.py

echo === Done ===
dir dist\%APP_NAME%.exe
pause

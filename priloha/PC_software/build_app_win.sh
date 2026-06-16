#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="LCR_meter"
PYTHON_VERSION="3.12.3"
PYTHON_INSTALLER="python-${PYTHON_VERSION}-amd64.exe"
PYTHON_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/${PYTHON_INSTALLER}"
WIN_PYTHON_DIR="wine_python"
WINE_PYTHON="wine $WIN_PYTHON_DIR/python.exe"

echo "=== Step 1: Download Windows Python installer ==="
if [ ! -f "$PYTHON_INSTALLER" ]; then
    wget -q --show-progress "$PYTHON_URL"
else
    echo "Already downloaded."
fi

echo "=== Step 2: Extract (no install, use embedded) ==="
rm -rf "$WIN_PYTHON_DIR"
mkdir -p "$WIN_PYTHON_DIR"
# Use the full installer to do an extraction via wine
wine "$PYTHON_INSTALLER" /quiet TargetDir="$(winepath -w "$SCRIPT_DIR/$WIN_PYTHON_DIR")" InstallAllUsers=0 Include_launcher=0 Include_test=0 Include_tools=0 Include_dev=0 Include_exe=1 Include_pip=1 Include_launcher=0 InstallLauncherAllUsers=0 2>&1 || true

# Alternative: Use the embeddable package instead if the full installer approach fails
echo "=== Step 3: Ensure pip is available ==="
$WINE_PYTHON -m ensurepip --upgrade 2>&1 || true

echo "=== Step 4: Install dependencies ==="
$WINE_PYTHON -m pip install --upgrade pip 2>&1 | tail -3
$WINE_PYTHON -m pip install pyinstaller pyqtgraph pyserial 2>&1 | tail -5

echo "=== Step 5: Build exe ==="
$WINE_PYTHON -m PyInstaller \
    --onefile \
    --windowed \
    --name "$APP_NAME" \
    --add-data "icon;." \
    --icon "icon.ico" \
    --collect-all "PySide6" \
    --clean \
    --noconfirm \
    LCR_meter_app.py 2>&1

echo ""
echo "=== Done ==="
ls -lh "dist/$APP_NAME.exe" 2>/dev/null || echo "dist/$APP_NAME.exe not found, check output above"

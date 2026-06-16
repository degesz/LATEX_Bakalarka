#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="LCR_meter"

source .venv/bin/activate

pip install pyinstaller 2>&1 | tail -3

pyinstaller \
    --onefile \
    --windowed \
    --name "$APP_NAME" \
    --add-data "icon:." \
    --icon "icon" \
    --clean \
    --noconfirm \
    LCR_meter_app.py

echo ""
echo "Done. Executable at: dist/$APP_NAME"

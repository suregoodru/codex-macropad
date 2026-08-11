#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
MACROPAD_USER_HOME=$(/usr/bin/python3 -c 'from pathlib import Path; print(Path.home())')

/usr/bin/python3 "$SCRIPT_DIR/installation.py" \
    uninstall \
    --home "$MACROPAD_USER_HOME"

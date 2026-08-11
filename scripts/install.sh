#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
MACROPAD_USER_HOME=$(/usr/bin/python3 -c 'from pathlib import Path; print(Path.home())')

"$SCRIPT_DIR/build-helper.sh"
/usr/bin/python3 "$SCRIPT_DIR/installation.py" \
    install \
    --home "$MACROPAD_USER_HOME" \
    --project-root "$PROJECT_ROOT"

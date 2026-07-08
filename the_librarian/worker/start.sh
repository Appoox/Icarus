#!/usr/bin/env bash
# worker/start.sh
# ────────────────────────────────────────────────────────────────────────
# One command to set up (first run only) and launch the Icarus worker's
# browser control panel. Safe to run every time you want to use the
# worker — it only does real setup work once; after that it just starts
# the server straight away.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv-worker"
MARKER="$VENV_DIR/.deps-installed"

# ── poppler-utils: a system package, not something pip can install ──────
if ! command -v pdftoppm >/dev/null 2>&1; then
    echo "poppler-utils isn't installed yet (needed to read PDF pages)."
    if command -v apt-get >/dev/null 2>&1; then
        echo "Installing it now — this may ask for your password."
        sudo apt-get update -qq && sudo apt-get install -y poppler-utils
    elif command -v brew >/dev/null 2>&1; then
        echo "Installing it now via Homebrew."
        brew install poppler
    else
        echo "Please install poppler-utils yourself, then run this script again:"
        echo "  Debian/Ubuntu/Mint: sudo apt install poppler-utils"
        echo "  macOS (Homebrew):   brew install poppler"
        exit 1
    fi
fi

# ── Python environment + dependencies: only the first time ──────────────
if [ ! -f "$MARKER" ]; then
    if [ ! -d "$VENV_DIR" ]; then
        echo "Setting up for the first time — this can take a few minutes..."
        python3 -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r requirements-worker.txt
    touch "$MARKER"
    echo "Setup complete."
fi

exec "$VENV_DIR/bin/python" worker_ui.py

#!/usr/bin/env bash
set -e

echo "=== ReconScope Setup Script ==="

# 1. Check Python version (requires >= 3.11)
if ! command -v python3 &>/dev/null; then
    echo "[-] Error: python3 is not installed."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[+] Detected Python version: $PY_VERSION"

# Simple version check
MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]; }; then
    echo "[-] Error: ReconScope requires Python 3.11 or higher."
    exit 1
fi

# 2. Create virtual environment if it does not exist
if [ ! -d ".venv" ]; then
    echo "[+] Creating virtual environment in .venv..."
    python3 -m venv .venv
else
    echo "[+] Virtual environment .venv already exists."
fi

# 3. Upgrade pip and install requirements
echo "[+] Installing/updating dependencies from requirements.txt..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 4. Install Playwright Chromium headless browser
echo "[+] Ensuring Playwright Chromium is installed..."
.venv/bin/playwright install chromium

# 5. Initialize SQLite Database
echo "[+] Initializing ReconScope database schema..."
.venv/bin/python3 -c 'from database.database import init_db; init_db(); print("Database initialized successfully.")'

echo ""
echo "=== Setup Complete! ==="
echo "To start ReconScope, run:"
echo "  source .venv/bin/activate"
echo "  python app.py"
echo ""
echo "Then navigate to http://127.0.0.1:8000 in your browser."

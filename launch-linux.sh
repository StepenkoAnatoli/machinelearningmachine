#!/usr/bin/env bash
# ============================================================
#  MachineLearningMachine - One-Click Launcher (Linux)
#  Double-click me (or: ./launch-linux.sh).
#  First run installs everything automatically.
# ============================================================
cd "$(dirname "$0")" || exit 1

echo
echo "============================================================"
echo "   MachineLearningMachine - One-Click Launcher (Linux)"
echo "============================================================"
echo

# ---------- 1. Find Python 3 ----------
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "[ERROR] Python 3 was not found on this computer."
  echo
  echo "  1. Install it, e.g.:"
  echo "       sudo apt install python3 python3-venv   (Debian/Ubuntu)"
  echo "       sudo dnf install python3                (Fedora)"
  echo "  2. Re-run this launcher."
  echo
  read -n 1 -s -r -p "Press any key to quit..."
  exit 1
fi
echo "[*] Using Python: $PY"

# ---------- 2. Create the private environment (first run only) ----------
if [ ! -x ".venv/bin/python" ]; then
  echo "[*] First run: creating a private Python environment (.venv) ..."
  if ! "$PY" -m venv .venv; then
    echo "[ERROR] Could not create the .venv environment."
    echo "        Debian/Ubuntu: install python3-venv and try again."
    echo "        Try running manually:  $PY -m venv .venv"
    exit 1
  fi
fi

# ---------- 3. Install dependencies (first run only) ----------
if [ ! -f ".venv/.deps_installed" ]; then
  echo "[*] First run: installing dependencies (one-time, a few minutes) ..."
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null
  if ! ./.venv/bin/python -m pip install -e .; then
    echo "[!] Editable install failed - falling back to requirements.txt ..."
    if ! ./.venv/bin/python -m pip install -r requirements.txt; then
      echo "[ERROR] The dependency install failed."
      echo "        Check your internet connection and run this launcher again."
      exit 1
    fi
  fi
  touch .venv/.deps_installed
fi

echo
echo "[*] Starting the dashboard at  http://127.0.0.1:8000"
echo "[*] Your browser will open in a moment."
echo "[*] Press Ctrl+C here to stop the app."
echo

# Open the browser a couple of seconds after the server starts
if command -v xdg-open >/dev/null 2>&1; then
  ( sleep 2 && xdg-open "http://127.0.0.1:8000" ) >/dev/null 2>&1 &
fi

exec ./.venv/bin/python -m machinelearningmachine.cli serve --host 127.0.0.1 --port 8000

echo
echo "[*] App stopped."

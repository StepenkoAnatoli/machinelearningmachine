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
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "[ERROR] This app needs Python 3.10 or newer; the one found here is older."
  echo
  echo "  Found: $("$PY" --version 2>&1)"
  echo "  1. Install a newer Python, e.g.:"
  echo "       sudo apt install python3 python3-venv   (Debian/Ubuntu)"
  echo "       sudo dnf install python3                (Fedora)"
  echo "  2. Re-run this launcher."
  echo
  read -n 1 -s -r -p "Press any key to quit..."
  exit 1
fi
echo "[*] Using Python: $PY  ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"

# ---------- 2. Create the private environment (first run only) ----------
if [ ! -x ".venv/bin/python" ] || ! ./.venv/bin/python -m pip --version >/dev/null 2>&1; then
  rm -rf .venv
  echo "[*] Creating a private Python environment (.venv) ..."
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
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1 || true
  if ! ./.venv/bin/python -m pip install -e .; then
    echo "[!] Editable install failed - falling back to standard install ..."
    if ! ./.venv/bin/python -m pip install .; then
      echo "[ERROR] The dependency install failed."
      echo "        Check your internet connection and run this launcher again."
      exit 1
    fi
  fi
  touch .venv/.deps_installed
fi

# ---------- 4. Choose a port the app can actually use ----------
# 8000 if it is free; otherwise the next free port, so a copy of the app left
# running from earlier cannot stop this one from starting.
PORT="$(./.venv/bin/python scripts/pick_port.py 2>/dev/null || echo 8000)"
case "$PORT" in ''|*[!0-9]*) PORT=8000 ;; esac
if [ "$PORT" != "8000" ]; then
  echo "[!] Port 8000 is busy, so the app will use port $PORT instead."
fi

echo
echo "[*] Starting the dashboard at  http://127.0.0.1:$PORT"
echo "[*] Your browser will open in a moment."
echo "[*] Press Ctrl+C here to stop the app."
echo

# Open the browser a couple of seconds after the server starts
if command -v xdg-open >/dev/null 2>&1; then
  ( sleep 2 && xdg-open "http://127.0.0.1:$PORT" ) >/dev/null 2>&1 &
fi

# `exec` hands the terminal to the server: Ctrl+C reaches the app itself,
# not this wrapper. The stopping message below it used to be unreachable.
exec ./.venv/bin/python -m machinelearningmachine.cli serve --host 127.0.0.1 --port "$PORT"

#!/usr/bin/env bash
# ============================================================
#  MachineLearningMachine - One-Click Launcher (macOS)
#  Double-click me. First run installs everything automatically.
# ============================================================
cd "$(dirname "$0")"

echo
echo "============================================================"
echo "   MachineLearningMachine - One-Click Launcher (macOS)"
echo "============================================================"
echo

# ---------- 1. Find Python 3 ----------
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "[ERROR] Python 3 was not found on this Mac."
  echo
  echo "  1. Open https://www.python.org/downloads/ and install the latest"
  echo "     Python 3 (or install Homebrew: brew install python3)"
  echo "  2. Re-run this launcher."
  echo
  read -n 1 -s -r -p "Press any key to quit..."
  exit 1
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "[ERROR] This app needs Python 3.10 or newer; the one found here is older."
  echo
  echo "  Found: $("$PY" --version 2>&1)"
  echo "  1. Open https://www.python.org/downloads/ and install the latest"
  echo "     Python 3 (or: brew install python3)"
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
( sleep 2 && open "http://127.0.0.1:$PORT" ) >/dev/null 2>&1 &

# `exec` hands the terminal to the server: Ctrl+C reaches the app itself,
# not this wrapper. The stopping message below it used to be unreachable.
exec ./.venv/bin/python -m machinelearningmachine.cli serve --host 127.0.0.1 --port "$PORT"

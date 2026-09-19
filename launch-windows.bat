@echo off
setlocal EnableExtensions
chcp 65001 >nul
title MachineLearningMachine Launcher
cd /d "%~dp0"

echo.
echo ============================================================
echo    MachineLearningMachine - One-Click Launcher (Windows)
echo ============================================================
echo.

rem ---------- 1. Find Python ----------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY where python3 >nul 2>nul && set "PY=python3"

if not defined PY (
    echo [ERROR] Python was not found on this computer.
    echo.
    echo   1. Open  https://www.python.org/downloads/
    echo   2. Download the latest "Python 3.10" or newer for Windows
    echo   3. IMPORTANT: tick "Add python.exe to PATH" during install
    echo   4. Re-run this launcher
    echo.
    pause
    exit /b 1
)
echo [*] Using Python: %PY%

rem ---------- 2. Create the private environment (first run only) ----------
if not exist ".venv\Scripts\python.exe" (
    echo [*] First run: creating a private Python environment (.venv) ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the .venv environment.
        echo         Try running manually:  %PY% -m venv .venv
        pause
        exit /b 1
    )
)
set "VENV_PY=.venv\Scripts\python.exe"

"%VENV_PY%" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [*] Recreating incomplete or corrupted .venv environment ...
    rmdir /s /q .venv 2>nul
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the .venv environment.
        echo         Try running manually:  %PY% -m venv .venv
        pause
        exit /b 1
    )
)

rem ---------- 3. Install dependencies (first run only) ----------
if not exist ".venv\.deps_installed" (
    echo [*] First run: installing dependencies (one-time, a few minutes) ...
    "%VENV_PY%" -m pip install --upgrade pip >nul 2>nul
    "%VENV_PY%" -m pip install -e .
    if errorlevel 1 (
        echo [!] Editable install failed - falling back to standard install ...
        "%VENV_PY%" -m pip install .
    )
    if errorlevel 1 (
        echo [ERROR] The dependency install failed.
        echo         Check your internet connection and run this launcher again.
        pause
        exit /b 1
    )
    echo ok > ".venv\.deps_installed"
)

echo.
echo [*] Starting the dashboard at  http://127.0.0.1:8000
echo [*] Your browser will open in a moment.
echo [*] Close this window (or press Ctrl+C) to stop the app.
echo.

rem Open the browser a couple of seconds after the server starts
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8000"

"%VENV_PY%" -m machinelearningmachine.cli serve --host 127.0.0.1 --port 8000

echo.
echo [*] App stopped.
pause
endlocal

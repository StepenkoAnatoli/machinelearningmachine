@echo off
setlocal EnableExtensions
chcp 65001 >nul
title MachineLearningMachine
cd /d "%~dp0"

echo.
echo ============================================================
echo    MachineLearningMachine - One-Click Launcher (Windows)
echo ============================================================
echo.
echo  First run:  installs what the app needs (a few minutes).
echo  Next runs:  starts in a couple of seconds.
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
    echo   2. Download the latest Python 3 for Windows and run the installer
    echo   3. IMPORTANT: on the first screen, tick "Add python.exe to PATH"
    echo   4. Double-click this file again
    echo.
    echo   No typing needed: follow INSTALL-WINDOWS.md in this folder.
    echo.
    pause
    exit /b 1
)

rem The "python" command on a PC without Python is a Microsoft Store placeholder:
rem it is found on PATH but cannot run anything. The check below catches that, and
rem an older Python, right here - instead of failing later with a wall of text.
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] This app needs Python 3.10 or newer.
    echo         The "python" found here is older than that, or is the Microsoft
    echo         Store placeholder rather than a real Python.
    echo.
    echo   1. Open  https://www.python.org/downloads/
    echo   2. Download the latest Python 3 for Windows and run the installer
    echo   3. IMPORTANT: on the first screen, tick "Add python.exe to PATH"
    echo   4. Close this window and double-click this file again
    echo.
    echo   Step-by-step guide: INSTALL-WINDOWS.md in this folder.
    echo.
    pause
    exit /b 1
)
%PY% -c "import sys; print('[*] Using Python ' + sys.version.split()[0] + '  (' + sys.executable + ')')"

rem ---------- 2. Create the private environment (first run only) ----------
if not exist ".venv\Scripts\python.exe" (
    echo [*] First run: creating a private environment in the .venv folder ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the private environment.
        echo         In a Command Prompt opened in this folder, try:
        echo             %PY% -m venv .venv
        echo.
        pause
        exit /b 1
    )
)
set "VENV_PY=.venv\Scripts\python.exe"

rem A .venv that exists but has no working pip (interrupted first run, moved
rem folder, antivirus) is rebuilt rather than used as-is.
"%VENV_PY%" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [*] The .venv folder is incomplete - rebuilding it ...
    rmdir /s /q .venv 2>nul
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not recreate the .venv environment.
        echo         Delete the .venv folder by hand, then run this file again.
        echo.
        pause
        exit /b 1
    )
)

rem ---------- 3. Install dependencies (first run only) ----------
if not exist ".venv\.deps_installed" (
    echo [*] First run: downloading the parts the app needs ...
    echo     This takes a few minutes and happens only once.
    "%VENV_PY%" -m pip install --upgrade pip >nul 2>nul
    "%VENV_PY%" -m pip install -e .
    if errorlevel 1 (
        echo [!] First attempt failed - trying a simpler install ...
        "%VENV_PY%" -m pip install .
    )
    if errorlevel 1 (
        echo [ERROR] The download/install step failed.
        echo         The usual reason is no internet connection, a company
        echo         proxy, or an antivirus blocking the download.
        echo         Check the connection, then double-click this file again.
        echo.
        pause
        exit /b 1
    )
    echo ok > ".venv\.deps_installed"
)

rem ---------- 4. Choose a port the app can actually use ----------
rem 8000 if it is free; otherwise the next free port, so a leftover copy of the
rem app cannot stop this one from starting.
set "PORT="
set "PORTFILE=%TEMP%\mlm_port_%RANDOM%.txt"
"%VENV_PY%" scripts\pick_port.py > "%PORTFILE%" 2>nul
rem Accept a bare number and nothing else; the value is checked in the file, so
rem nothing that could be mistaken for a command ever reaches this script.
findstr /r "^[0-9][0-9]*$" "%PORTFILE%" >nul 2>nul
if not errorlevel 1 set /p PORT=<"%PORTFILE%"
del "%PORTFILE%" >nul 2>nul
if not defined PORT set "PORT=8000"
if not "%PORT%"=="8000" (
    echo [!] Port 8000 is busy, so the app will use port %PORT% instead.
)

rem ---------- 5. Start the dashboard ----------
echo.
echo [*] Starting the dashboard at  http://127.0.0.1:%PORT%
echo [*] Your browser should open by itself in a few seconds.
echo     If it does not, type this address into your browser:
echo         http://127.0.0.1:%PORT%
echo.
echo [*] Leave this window open while you use the app.
echo     To stop the app: close this window, or press Ctrl+C.
echo.

rem Open the browser a couple of seconds after the server starts
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:%PORT%"

"%VENV_PY%" -m machinelearningmachine.cli serve --host 127.0.0.1 --port %PORT%

echo.
echo [*] The app has stopped.
echo     If it stopped on its own, the messages above say why.
echo     Double-click this file to start it again.
echo.
pause
endlocal

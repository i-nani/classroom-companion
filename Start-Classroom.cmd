@echo off
setlocal
title Classroom Companion
cd /d "%~dp0"
if not exist "run_local.py" goto extract
if not exist "companion\__main__.py" goto extract
echo Starting Classroom Companion...
echo First launch needs Python 3.10 or newer and internet for the Windows timezone package.
set "PYTHONUTF8=1"
set "PYTHON_CHECK=import sys; sys.exit(sys.version_info[:2] < (3, 10))"
if not exist ".venv\Scripts\python.exe" goto findpython
".venv\Scripts\python.exe" -c "%PYTHON_CHECK%" >nul 2>&1
if not errorlevel 1 goto install
:findpython
set "CLASSROOM_PYTHON="
for %%P in ("py -3" "py -3.14" "py -3.13" "py -3.12" "py -3.11" "py -3.10" "python" "python3") do (
    if not defined CLASSROOM_PYTHON (
        %%~P -c "%PYTHON_CHECK%" >nul 2>&1
        if not errorlevel 1 set "CLASSROOM_PYTHON=%%~P"
    )
)
if not defined CLASSROOM_PYTHON goto nopython
echo Creating the project environment with %CLASSROOM_PYTHON%...
%CLASSROOM_PYTHON% -m venv .venv
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
set "CLASSROOM_MODE=%~1"
if not defined CLASSROOM_MODE set "CLASSROOM_MODE=offline"
".venv\Scripts\python.exe" run_local.py "%CLASSROOM_MODE%"
if errorlevel 1 goto fail
exit /b 0
:nopython
echo No working Python 3.10 or newer was found.
echo Install Python for Windows from https://www.python.org/downloads/windows/ or repair your existing installation.
echo Then close this window and run Start-Classroom.cmd again.
pause
exit /b 1
:extract
echo Extract the entire ZIP, then run Start-Classroom.cmd from the folder containing run_local.py.
pause
exit /b 1
:fail
echo Check the error above. This launcher requires Python 3.10 or newer.
pause
exit /b 1

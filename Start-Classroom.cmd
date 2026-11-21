@echo off
setlocal
title Classroom Companion
cd /d "%~dp0"
if not exist "run_local.py" goto extract
if not exist "companion\__main__.py" goto extract
echo Starting Classroom Companion...
echo First launch needs Python 3.10 or newer and internet for the Windows timezone package.
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 -m venv .venv
if errorlevel 1 goto fail
goto install
:usepython
python -m venv .venv
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
set "CLASSROOM_MODE=%~1"
if not defined CLASSROOM_MODE set "CLASSROOM_MODE=offline"
".venv\Scripts\python.exe" run_local.py "%CLASSROOM_MODE%"
if errorlevel 1 goto fail
exit /b 0
:extract
echo Extract the entire ZIP, then run Start-Classroom.cmd from the folder containing run_local.py.
pause
exit /b 1
:fail
echo Check the error above. This launcher requires Python 3.10 or newer.
pause
exit /b 1

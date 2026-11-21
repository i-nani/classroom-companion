@echo off
setlocal
cd /d "%~dp0"
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
".venv\Scripts\python.exe" run_local.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo Check the error above. This launcher requires Python 3.10 or newer.
pause
exit /b 1

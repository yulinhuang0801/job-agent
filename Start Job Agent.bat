@echo off
rem Double-click to start Job Agent on Windows. Needs Python 3.9+ from python.org (tick "Add python.exe to PATH").
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Setting up Job Agent ^(the first run takes a minute or two^)...
  py -3 -m venv .venv 2>nul || python -m venv .venv 2>nul || goto nopython
)
fc /b requirements.txt .venv\installed-requirements.txt >nul 2>nul || (
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -r requirements.txt || goto failed
  copy /y requirements.txt .venv\installed-requirements.txt >nul
)
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
echo Job Agent is running at http://127.0.0.1:8000 ^(keep this window open; close it to stop^).
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto :eof
:nopython
echo Job Agent needs Python 3.9 or newer. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" in the installer), then double-click this file again.
pause
goto :eof
:failed
echo Installing the libraries failed. Check your internet connection and try again.
pause

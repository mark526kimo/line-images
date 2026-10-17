@echo off
cd /d "%~dp0"

rem Find Python: prefer the "py" launcher, then "python"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython

if exist ".venv\Scripts\python.exe" goto install
echo [1/2] First run: creating environment, please wait...
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto nopython

:install
echo [2/2] Installing / checking packages, please wait (first run takes a few minutes)...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto piperror

echo Starting... (keep this window open)
".venv\Scripts\python.exe" voice_to_srt.py
if errorlevel 1 pause
exit /b 0

:nopython
echo.
echo Python not found.
echo Please install Python 3.11 or newer from https://www.python.org/downloads/
echo and CHECK "Add python.exe to PATH" on the first setup screen.
echo.
pause
exit /b 1

:piperror
echo.
echo Package install failed. Please check your internet connection and try again.
echo.
pause
exit /b 1

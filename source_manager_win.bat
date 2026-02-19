@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
  set "PY=python"
) else (
  where py >nul 2>nul
  if %errorlevel%==0 (
    set "PY=py -3"
  ) else (
    echo Python 3 was not found.
    echo Install Python from https://www.python.org/downloads/windows/
    pause
    exit /b 1
  )
)

echo Starting Source Registry UI...
echo URL: http://127.0.0.1:8765
start "" http://127.0.0.1:8765
%PY% code\tools\sources\ui_local.py

echo.
echo Source Registry UI stopped.
pause

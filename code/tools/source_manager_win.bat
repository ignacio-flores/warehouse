@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

if "%SOURCE_MANAGER_HOST%"=="" (
  set "HOST=127.0.0.1"
) else (
  set "HOST=%SOURCE_MANAGER_HOST%"
)
if "%SOURCE_MANAGER_PORT%"=="" (
  set "PORT_START=8765"
) else (
  set "PORT_START=%SOURCE_MANAGER_PORT%"
)

echo ADAM SSM - Sleepless Source Manager
echo Working directory: %REPO_ROOT%
echo Requested bind: %HOST%:%PORT_START%

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY_CMD=py -3"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    set "PY_CMD=python"
  ) else (
    echo Python 3 was not found.
    echo Install Python from https://www.python.org/downloads/windows/ and try again.
    pause
    exit /b 1
  )
)

echo Python command: %PY_CMD%
%PY_CMD% --version
echo.

%PY_CMD% code\tools\sources\launch_source_manager.py --host "%HOST%" --port "%PORT_START%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Launcher exit code: %EXIT_CODE%
pause
exit /b %EXIT_CODE%

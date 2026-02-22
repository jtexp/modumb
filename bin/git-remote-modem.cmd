@echo off
REM Git remote helper for modem:// URLs on Windows
REM This wrapper calls the Python module directly

setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
set "VENV_PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "PYTHONPATH=%PROJECT_DIR%\src;%PYTHONPATH%"

if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -m modumb.git.remote_helper %*
) else (
    python -m modumb.git.remote_helper %*
)

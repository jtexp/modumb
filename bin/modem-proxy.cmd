@echo off
REM Modem HTTP Proxy (Machine A) on Windows
REM This wrapper calls the Python module directly

setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
set "VENV_PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "PYTHONPATH=%PROJECT_DIR%\src;%PYTHONPATH%"

if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -m modumb.proxy.local_proxy %*
) else (
    python -m modumb.proxy.local_proxy %*
)

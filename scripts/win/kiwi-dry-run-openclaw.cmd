@echo off
rem kiwi-dry-run-openclaw.cmd - OpenClaw CLI shim for Kiwi dry-run mode.
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Invoke-KiwiDryRunOpenClaw.ps1" %*
exit /b %ERRORLEVEL%

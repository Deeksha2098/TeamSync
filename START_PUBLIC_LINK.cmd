@echo off
setlocal
cd /d "%~dp0"
title TeamSync - Public Meeting Link
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_public_localtunnel.ps1"
endlocal

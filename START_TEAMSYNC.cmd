@echo off
setlocal
cd /d "%~dp0"
title TeamSync - Local App
start "TeamSync Backend" cmd /k "call "%~dp0run_backend.bat""
timeout /t 3 /nobreak >nul
start "TeamSync Frontend" cmd /k "call "%~dp0run_frontend.bat""
timeout /t 6 /nobreak >nul
start "" "http://localhost:5173"
echo.
echo TeamSync is running locally at http://localhost:5173
 echo.
echo For WhatsApp/cross-device meetings, run START_PUBLIC_LINK.cmd and keep it open.
pause
endlocal

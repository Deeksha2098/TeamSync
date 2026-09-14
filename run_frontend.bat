@echo off
cd /d "%~dp0frontend"
if not exist node_modules (
  echo Installing frontend packages...
  npm install
)
if exist node_modules\esbuild\install.js node node_modules\esbuild\install.js >nul 2>&1
npm run dev -- --host 0.0.0.0

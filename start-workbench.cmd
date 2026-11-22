@echo off
setlocal
cd /d "%~dp0"

if not exist "web\index.html" (
  echo Built frontend missing. See README.md for build instructions.
  goto failed
)

if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if errorlevel 1 (
    python -m venv .venv
  ) else (
    py -3 -m venv .venv
  )
  if errorlevel 1 goto failed
)

if not exist ".venv\workbench-deps-v05" (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
  type nul > ".venv\workbench-deps-v05"
)

if /i "%~1"=="--playwright" (
  if not exist ".venv\workbench-playwright-installed" (
    ".venv\Scripts\python.exe" -m pip install "playwright>=1.50,<2"
    if errorlevel 1 goto failed
    ".venv\Scripts\python.exe" -m playwright install chromium
    if errorlevel 1 goto failed
    type nul > ".venv\workbench-playwright-installed"
  )
  set "LISTING_AMAZON_PLAYWRIGHT=1"
)

echo.
echo Open the address shown after "Uvicorn running on" and keep this window open.
echo.
".venv\Scripts\python.exe" -m backend.serve
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Startup failed. Check the message above and the README.
pause
exit /b 1

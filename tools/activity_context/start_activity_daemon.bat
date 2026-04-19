@echo off
setlocal
REM Go to repo root (this bat lives in tools\activity_context\)
cd /d "%~dp0..\.."

if not exist "venv\Scripts\python.exe" (
  echo [ERROR] Missing venv\Scripts\python.exe
  echo Create venv at repo root, or edit this bat to use your Python path.
  pause
  exit /b 1
)

echo Starting activity daemon. Every 30 min: collect -^> summarize -^> upload
echo Press Ctrl+C to stop.
echo.

"venv\Scripts\python.exe" -m tools.activity_context.daemon
set EXITCODE=%ERRORLEVEL%
echo.
echo Exit code: %EXITCODE%
pause
exit /b %EXITCODE%

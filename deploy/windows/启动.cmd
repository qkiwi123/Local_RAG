@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
"%~dp0runtime\python.exe" "%~dp0launcher\portable.py" start %*
if errorlevel 1 (
  pause
  exit /b 1
)
exit /b 0

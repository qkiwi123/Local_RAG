@echo off
setlocal
"%~dp0..\runtime\python.exe" "%~dp0probe_w1.py" --device cpu --expect-no-driver --report no-driver-cpu.json
set "w1_exit_code=%ERRORLEVEL%"
if not "%w1_exit_code%"=="0" (
  echo W1 CPU verification FAILED. See reports\no-driver-cpu.json
) else (
  echo W1 CPU verification PASSED. See reports\no-driver-cpu.json
)
pause
exit /b %w1_exit_code%

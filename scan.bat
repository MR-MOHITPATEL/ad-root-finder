@echo off
REM Ad Root Finder — nightly competitor scan.
REM Runs from a residential IP (this PC). Writes to Supabase.
REM Wire into Task Scheduler with "run task as soon as possible after a
REM scheduled start is missed" so it catches up whenever the PC is on.

cd /d "%~dp0"
set PYTHONUNBUFFERED=1

echo ============================================
echo  scan started  %date% %time%
echo ============================================

python run.py scan >> "%~dp0scan.log" 2>&1

echo  scan finished  %date% %time% >> "%~dp0scan.log"

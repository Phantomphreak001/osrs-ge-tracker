@echo off
cd /d "%~dp0"
where py >nul 2>nul && (start "" pyw -3 ge_tracker.py) || (start "" pythonw ge_tracker.py)

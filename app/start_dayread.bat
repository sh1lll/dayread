@echo off
rem Windows: double-click to start Day Read in your browser.
cd /d "%~dp0"
if exist "..\.venv\Scripts\python.exe" ("..\.venv\Scripts\python.exe" dayread_app.py %*) else (where py >nul 2>nul && (py -3 dayread_app.py %*) || (python dayread_app.py %*))
pause

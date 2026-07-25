@echo off
REM One-click launcher for the Job Application Pipeline web app.
REM Double-click this file. It starts the local server and opens your browser.
cd /d "%~dp0"
"%~dp0job_pipe_env\Scripts\python.exe" "%~dp0run_app.py"
pause

@echo off
rem Launch the app. Uses pythonw (no black console window) if available, else python.
rem Requires Python on your PATH (see README "Quick start").
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0meeting_assistant.py"
) else (
  python "%~dp0meeting_assistant.py"
)

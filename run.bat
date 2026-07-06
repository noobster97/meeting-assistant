@echo off
rem Launch the GUI with pythonw (no black console window).
set PYW=C:\Users\rumai\AppData\Local\Programs\Python\Python312\pythonw.exe
if not exist "%PYW%" set PYW=pythonw
start "" "%PYW%" "%~dp0meeting_assistant.py"

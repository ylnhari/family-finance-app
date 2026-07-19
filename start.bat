@echo off
rem Prefer the local venv (has the optional investments extras) when present.
if exist "%~dp0.venv\Scripts\python.exe" ("%~dp0.venv\Scripts\python.exe" "%~dp0server.py" %*) else (where python >nul 2>nul && (python "%~dp0server.py" %*) || (py "%~dp0server.py" %*))

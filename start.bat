@echo off
where python >nul 2>nul && (python server.py %*) || (py server.py %*)

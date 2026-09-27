@echo off
cd /d "%~dp0"
if errorlevel 1 exit /b
echo == Python tests (server, persistence, gemini logic) ==
call python -m unittest discover -s tests -p "test_*.py"
if errorlevel 1 exit /b
echo.
echo == JS tests (financial math + sample data) ==
node --test tests/math.test.js tests/sample.test.js tests/filter.test.js
exit /b

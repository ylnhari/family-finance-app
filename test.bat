@echo off
cd /d "%~dp0"
echo == Python tests (server, persistence, gemini logic) ==
python -m unittest discover -s tests -p "test_*.py"
echo.
echo == JS tests (financial math + sample data + companion URL policy) ==
node --test tests/math.test.js tests/sample.test.js tests/companion-url.test.js
pause

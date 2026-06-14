#!/usr/bin/env bash
# Run all tests — pure-math (Node) + server/API (Python stdlib). No dependencies.
set -e
cd "$(dirname "$0")"
echo "== Python tests (server, persistence, gemini logic) =="
python -m unittest discover -s tests -p "test_*.py"
echo
echo "== JS tests (financial math + sample-data integrity) =="
node --test tests/math.test.js tests/sample.test.js

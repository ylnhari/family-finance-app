# Contributing to Family Finance

Thanks for your interest! This is a small, dependency-free app and we'd like to keep it that
way. A few ground rules make contributions easy to review and merge.

## Golden rules

1. **No new dependencies.** The server is Python **standard library only** (no Flask, no
   FastAPI, no `pip install`). Tests use Python's built-in `unittest` and Node's built-in test
   runner. The frontend is plain HTML/CSS/JS — CDN scripts only if truly unavoidable.
2. **Data stays local.** No code path may transmit `data/finances.json` anywhere. The optional
   Gemini endpoints stay key-gated and must send only an uploaded payslip or a goal name.
3. **Never touch real data.** Don't read, edit, move, or commit anything under `data/`. For
   manual checks use `python server.py --demo` (writes to `demo-data/`) or `--data-dir`.
4. **Keep money math pure.** All financial calculations live in `public/finance-math.js`
   (no DOM, no I/O) so they stay unit-testable. `app.js` only orchestrates UI + persistence.

## Every change ships with tests + demo data

A change is not "done" until **both** are updated:

- **Tests** proving the new behaviour:
  - pure calc → add to `public/finance-math.js`, export it, add cases to `tests/math.test.js`
  - endpoint → add happy + error cases to `tests/test_server.py`
  - AI parsing → `tests/test_gemini.py`
- **Sample data** — extend `samples/demo-finances.json` so the feature is demoable, and assert
  its presence in `tests/sample.test.js`.

## Running the tests

```bash
./test.sh        # everything (Windows: test.bat)

# or individually:
python -m unittest discover -s tests -p "test_*.py"
node --test tests/math.test.js tests/sample.test.js tests/companion-url.test.js
```

CI runs the full suite on Linux and Windows (Python 3.10 & 3.12). Please make sure it's green
before opening a PR.

## Development tips

- Server changes: restart and verify the browser UI loads, then run the suite.
- Frontend changes: hard-refresh the browser (Ctrl+Shift+R) to clear cache.
- Run on a chosen port with `python server.py --port 8765` (a clone has no `ports.json`).

## Commit / PR style

- Keep commits focused; describe the *why*, not just the *what*.
- One feature or fix per PR where possible.
- Update `CHANGELOG.md` under `[Unreleased]`.

By contributing you agree your work is licensed under the project's [MIT License](LICENSE).

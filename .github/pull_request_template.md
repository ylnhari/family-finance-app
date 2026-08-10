## What & why

<!-- What does this change do, and why? -->

## Checklist

- [ ] No new runtime dependencies (stdlib-only server; built-in test runners)
- [ ] No code path reads/transmits real `data/finances.json`
- [ ] Money math (if any) lives in `public/finance-math.js` and is unit-tested
- [ ] Tests added/updated (`math.test.js` / `sample.test.js` / `test_server.py` / `test_gemini.py`)
- [ ] Sample data updated + asserted in `sample.test.js` (if a user-visible feature)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `./test.sh` (or `test.bat`) passes locally

# Claude Instructions — family-finance-app

> **Setup:** copy `CLAUDE.local.md.example` → `CLAUDE.local.md` (gitignored, auto-loaded) and fill in your machine-local values. Personal/global preferences live in your user-level `~/.claude/CLAUDE.md`.

## Role
Maintain a self-contained local finance tracker. No cloud, no accounts, no external deps — Python stdlib + browser only.

## Context
A self-contained personal/family finance tracker. Tracks income, expenses, loans, cards. Data lives in `data/finances.json` (gitignored). Server is `server.py` (stdlib only); default port 8765 — see `CLAUDE.local.md`.

**Privacy rule:** `data/` is gitignored. Never read, log, or transmit the contents of `data/finances.json` to any external service.

## Architecture

```
server.py              # Stdlib HTTP server (no Flask, no FastAPI). Optional Gemini AI
                       #   endpoints (payslip extract, goal-price) — key-gated, degrade gracefully
                       #   Also serves GET /invest (public/invest.html) + /api/invest/* + broker
                       #   OAuth callbacks (/auth/<kite|upstox>/<account>/<login|callback>)
config.py               # Investments module config: ports.json port lookup + .env loader
                       #   (repo .env, then ../.env; FF_NO_DOTENV=1 disables, used by tests)
invest_api.py           # /api/invest/* route handlers + broker OAuth callback handlers
invest_cli.py           # CLI: import / ipo / wint subcommands (holdings CSV, IPO tracker, Wint xlsx)
daily_brief.py          # Optional: daily portfolio/IPO summary pushed to phone via ntfy.sh
refresh_tokens.py       # Optional: unattended morning broker token refresh (TOTP 2FA)
investlib/              # Investments package: brokers.py, analysis.py, ipo.py, ipo_fetch.py,
                       #   ipo_history.py, portfolio.py, store.py, wintwealth.py, xlsx_lite.py
requirements-invest.txt # OPTIONAL extras (kiteconnect, upstox-python-sdk, requests, pyotp) —
                       #   only for live broker/NSE/IPO network sync; nothing else needs them
public/
  index.html          # loads finance-math.js BEFORE app.js
  invest.html          # investments dashboard UI (served at /invest)
  finance-math.js     # PURE money math (EMI, amortization, loan prepayment, income totals,
                       #   gold gain, validation). No DOM/DB — require()-able + unit-tested
  app.js              # UI + DB orchestration; delegates calc to finance-math.js
  style.css
samples/
  demo-finances.json  # Committed fake dataset (NOT under data/). Demo + test fixture
tests/                 # Regression suite — see TESTPLAN.md
data/                  # YOUR data — gitignored, never commit
  finances.json        # All records
  files/               # Uploaded documents
  backups/             # Auto daily backups (last 14 kept)
  invest/              # Investment holdings/accounts/IPO tracker JSON — same gitignore rules
imports/               # Broker CSV/xlsx statement drops for invest_cli.py — gitignored except README
demo-data/             # Throwaway --demo sandbox — gitignored, auto-seeded from samples/ (also seeds
                       #   fake invest holdings)
.env / .env.example    # Gitignored secrets (broker API keys, GEMINI_API_KEY, NTFY_TOPIC) /
                       #   tracked template with every value left blank
start.bat / start.sh   # Launchers (start.bat auto-prefers .venv\Scripts\python.exe if present)
test.bat  / test.sh    # Test runners
```

## Rules
1. **No new dependencies, with one sanctioned exception.** The core server stays
   stdlib-only for everyone; the investments module's live-sync extras
   (`requirements-invest.txt`: kiteconnect, upstox-python-sdk, requests, pyotp) are the
   single sanctioned exception, and they exist **only** to talk to broker/NSE APIs.
   They must stay optional and lazily imported — the app, including the investments
   dashboard's CSV/xlsx import and manual tracking, must run with no installs at all;
   missing extras degrade to a clear error on the sync buttons, never a crash. Node's
   built-in test runner + Python `unittest` for tests. Frontend may use CDN scripts only
   if absolutely necessary. No other new dependencies without asking.
2. **Data stays local.** No API calls from `server.py` that transmit financial data anywhere. (The Gemini endpoints send an uploaded payslip / a goal *name* only — never `finances.json` — and only when the user clicks and a key is set.)
3. **Never touch the user's `data/` folder.** Don't read, edit, move, delete, or commit anything under `data/` or `imports/`. For demos/manual checks use `--demo` (writes to `demo-data/`, gitignored) or a temp `--data-dir`. Keep `data/`, `imports/`, and `demo-data/` gitignored — verify `.gitignore` before any git op.
4. **Backups are automatic** — don't delete `data/backups/` manually.
5. **Tests must stay green.** Run `./test.sh` (or `test.bat`) before considering any change done.
6. **Every feature ships with tests + demo data.** No change is "done" until BOTH are updated:
   - **Tests** — add cases proving the new behaviour (pure calc → `math.test.js`; endpoint → `test_server.py`; AI parse → `test_gemini.py`).
   - **Sample data** — extend `samples/demo-finances.json` so the feature is visibly demoable, and assert its presence in `sample.test.js` so the demo can't silently lose coverage.

## Investments module rules
- **Signals are decision-support, not advice.** Buy/sell/keep signals and the IPO
  apply/skip recommendation are simple heuristics, not financial advice — no trading
  automation or order placement, ever.
- **Thresholds are named constants**, kept at the top of `investlib/analysis.py` and
  `investlib/ipo.py` — never inline magic numbers.
- **Tests never touch real `data/`** — use the `TempDataMixin` pattern (swaps
  `config.DATA_DIR` to a temp dir) — **and never call broker/NSE/IPO networks.**
- **Personal financial data only lives in gitignored `data/` and `imports/`.** Never
  commit holdings, account numbers, or broker CSV/xlsx exports; test fixtures use fake
  symbols/accounts only.

## Running
```bash
python server.py                 # real data — opens http://127.0.0.1:8765
python server.py --port 9000 --no-browser
python server.py --demo          # fake dataset in demo-data/ — real data/ untouched
```

## Testing
Two stdlib suites, no installs. See `tests/TESTPLAN.md` for the full plan.
```bash
./test.sh            # everything (Windows: test.bat)
python -m unittest discover -s tests -p "test_*.py"   # server, persistence, gemini parse logic
node --test tests/math.test.js tests/sample.test.js   # money math + sample-data integrity
```
- **`tests/math.test.js`** — pure calcs from `finance-math.js` (EMI, amortization, prepayment, income totals, gold gain, validation).
- **`tests/sample.test.js`** — runs real calcs over `samples/demo-finances.json`; guards both data drift and math regressions.
- **`tests/test_server.py`** — boots a real server on a temp dir; GET/PUT, **concurrent-write lock**, file lifecycle, path-traversal, backup/restore, `--demo` isolation, AI graceful-degrade (no quota burn).
- **`tests/test_gemini.py`** — Gemini JSON-fence / price-text parsing + model fallback ordering (no network).

**When you change behaviour, extend the suite:**
- New pure calc → add it to `finance-math.js`, export it, add cases to `math.test.js`.
- New endpoint → add happy + error cases to `test_server.py`.
- New demo-data feature → assert its presence in `sample.test.js`.

## When editing
- Server changes: restart and verify the browser UI loads; run the suite.
- Frontend changes: hard-refresh the browser (Ctrl+Shift+R) to clear cache.
- Keep all money math in `finance-math.js` (testable), not inline in `app.js`.
- Never add a feature that *requires* an external service call (AI stays optional and key-gated).

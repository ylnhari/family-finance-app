# Codex Instructions — family-finance-app

> **Setup:** `Codex.local.md` is an optional ignored host-local overlay. Do not assume a loader reads it: verify actual host support before relying on it. Keep machine values in the documented local configuration boundary.

## Role
Maintain a self-contained local finance tracker. No cloud, no accounts, no external deps — Python stdlib + browser only.

## Context
A self-contained personal/family finance tracker. Tracks income, expenses, loans, cards. Data lives in `data/finances.json` (gitignored). Server is `server.py` (stdlib only); local runtime values require verified configuration loading.

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
  invest.html          # investments dashboard UI (served at /invest): onboarding hero when
                       #   zero accounts, account CRUD, manual-holdings editor. Has its own
                       #   esc() — escape every dynamic string (see rule 7)
  finance-math.js     # PURE money math (EMI, amortization, loan prepayment, income totals,
                       #   gold gain, validation). No DOM/DB — require()-able + unit-tested
  app.js              # UI + DB orchestration; delegates calc to finance-math.js
  theme.js            # ONE theme + hide-values preference shared by index.html and
                       #   invest.html (single `ffa_theme` key, cross-tab/iframe sync).
                       #   Don't add a second per-page toggle — they drift apart
  style.css           # light + [data-theme="dark"] palettes. SVG/chart colours must use
                       #   the CSS custom properties, never hardcoded hex (breaks a theme)
docs/                  # BROKER-SETUP.md, NOTIFICATIONS.md — served at /docs/<name>.md by a
                       #   small markdown renderer in server.py, linked from the invest UI
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
5. **Tests must stay green — in an environment that matches rule 1.** Run `./test.sh`
   (or `test.bat`) before considering any change done. **Green on your machine is not
   green:** most dev machines have `requests` (and friends) installed, so a change that
   breaks the zero-install promise still passes locally. CI installs no extras, and it
   has already caught an import-time use of an optional dep that stopped the server from
   starting at all. Before calling anything done, also run the suite with the extras
   absent:
   ```bash
   python -m venv .venv-noextras                  # once; do NOT install anything into it
   .venv-noextras/bin/python     -m unittest discover -s tests -p "test_*.py"   # Mac/Linux
   .venv-noextras\Scripts\python -m unittest discover -s tests -p "test_*.py"   # Windows
   ```
   `tests/test_no_extras.py` guards this automatically — it imports every module with
   `requests`/`kiteconnect`/`pyotp`/`upstox_client` forced absent. Never weaken it.
6. **Every feature ships with tests + demo data.** No change is "done" until BOTH are updated:
   - **Tests** — add cases proving the new behaviour (pure calc → `math.test.js`; endpoint → `test_server.py`; AI parse → `test_gemini.py`).
   - **Sample data** — extend `samples/demo-finances.json` so the feature is visibly demoable, and assert its presence in `sample.test.js` so the demo can't silently lose coverage.
7. **This is an open-source app — the blank slate is a first-class state.** Someone else
   clones this with an empty data dir, no `.env`, no broker accounts and no installs.
   Every feature must work, or degrade honestly, from that starting point:
   - **User data is never a constant.** Accounts, persons, owners, family members and
     account ids are things a user CREATES in the UI. Never ship them as defaults in
     code, and never hardcode your own setup as a fallback — a fresh install shows an
     empty state with a way to add the thing, not somebody else's data. (`investlib/
     store.py` used to seed seven real accounts this way; don't reintroduce it.)
   - **Every empty state needs a way out** — an "add" affordance or a doc link, never a
     blank panel or a dead end. If the UI advertises a path ("track manually"), that
     path must be reachable from the UI, not just from the API.
   - **Setup that happens outside the app gets a doc** — see `docs/BROKER-SETUP.md` and
     `docs/NOTIFICATIONS.md`, linked from the UI where the user needs them.
   - **Nothing personal in tracked files** — no names, client ids, real ISINs, machine
     paths, ports, IPs or topics, in code, tests, demo data or docs. Machine-local values
     belong in gitignored `.env` / `Codex.local.md`; `.env.example` ships blank.
   - **Escape everything you render.** `public/app.js` and `public/invest.html` each have
     an `esc()` helper; user- and broker-supplied strings (labels, symbols, IPO names,
     filenames) go through it. A missing escape here was a real stored-XSS bug.
   - **Say where a number came from.** Broker-synced, imported and hand-typed values must
     stay distinguishable in the UI (the `source` field feeds the live/synced badge).

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
  symbols/accounts and fake people only — no real names, client ids or ISINs.
- **Accounts come from the store, not from code.** `investlib/store.py` ships NO default
  accounts and `investlib/brokers.py` has no hardcoded account list — broker-capable
  accounts are whatever the user created (rule 7). Credentials are per-account env vars
  derived from the account id (`kite-1` → `KITE_1_API_KEY`, `_API_SECRET`, `_USER_ID`,
  `_PASSWORD`, `_TOTP_SECRET`, `_ANALYTICS_TOKEN`); adding an account is a UI action,
  never an edit to a Python tuple.
- **Demo invest data is seeded fake** by `ensure_demo_invest_data()` in `server.py`, and a
  test asserts every demo owner is in its `FAKE_DEMO_OWNERS` allowlist. Keep that guard.

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
node --test tests/math.test.js tests/sample.test.js
                                                    # math + sample data
```
- **`tests/math.test.js`** — pure calcs from `finance-math.js` (EMI, amortization, prepayment, income totals, gold gain, validation).
- **`tests/sample.test.js`** — runs real calcs over `samples/demo-finances.json`; guards both data drift and math regressions.
- **`tests/test_server.py`** — boots a real server on a temp dir; GET/PUT, **concurrent-write lock**, file lifecycle, path-traversal, backup/restore, `--demo` isolation, blank-slate + account CRUD, broker OAuth login/callback and sync (mocked — never live), AI graceful-degrade (no quota burn).
- **`tests/test_gemini.py`** — Gemini JSON-fence / price-text parsing + model fallback ordering (no network).
- **`tests/test_investlib.py`** — investlib units (portfolio/bridge/analysis/ipo/wint/store) on a temp data dir with a fake account registry.
- **`tests/test_cli.py`** — `invest_cli.py` import / ipo / wint subcommands against temp dirs.
- **`tests/test_docs.py`** — the `/docs/*.md` route + markdown renderer (tables, paragraphs, list continuations, HTML-escaping, path traversal).
- **`tests/test_no_extras.py`** — imports every module with the optional extras forced absent; the guard for rule 1 + rule 5.

**When you change behaviour, extend the suite:**
- New pure calc → add it to `finance-math.js`, export it, add cases to `math.test.js`.
- New endpoint → add happy + error cases to `test_server.py`.
- New demo-data feature → assert its presence in `sample.test.js`.
- New optional-dep usage → make sure it's lazy, and confirm `test_no_extras.py` still passes.
- New UI surface → check how it looks with ZERO data, and in BOTH themes.

## When editing
- Server changes: restart and verify the browser UI loads; run the suite.
- Frontend changes: hard-refresh the browser (Ctrl+Shift+R) to clear cache.
- Keep all money math in `finance-math.js` (testable), not inline in `app.js`.
- Never add a feature that *requires* an external service call (AI stays optional and key-gated).

## Imported Claude Cowork project instructions

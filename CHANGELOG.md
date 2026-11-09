# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and the project
uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Changed
- **Investments dashboard (`/invest`) leads with answers, not data sources.** The page used to open on
  broker sync and three IPO lists, with per-account values about 18,000 px down. It now shows total
  value, invested and gain, a "Needs your attention" strip, a card per person (select one to filter), and
  an Accounts table (value, invested, gain, data age) first. IPOs, bonds, flagged positions and
  broker sync / account management are collapsed sections with one-line summaries; their content and
  controls are unchanged, and the open/closed choice is remembered. Powered by the new
  `GET /api/invest/overview` (`investlib/overview.py`).
- **Invested and gain now use one rule everywhere** (`portfolio.position_totals`). A holding with a
  quantity but no known cost basis (a bonus or transferred-in position) is carried at its current value,
  so it no longer inflates the Portfolio page's return; the dashboard footnotes how many there are.

### Fixed
- **Portfolio live rows no longer claim to be freshly updated.** `investlib/bridge.py` stamped
  every synced row `lastSynced = now`, so a snapshot that had not refreshed for weeks still read
  "⟳ live, last updated just now". Rows now carry the snapshot's own date plus `ageDays` /
  `stale` (broker-API sources after 4 days, imports after 30); the badge turns amber
  ("⚠ Nd old") when stale.
- **A broker sync that is failing is now visible.** `brokers.sync()` records every outcome in
  `data/invest/sync_status.json`; `GET /api/invest/tokens` returns it as `sync`, and the Broker
  sync table shows "sync failing" with the reason beside a token that still looks valid
  (previously a rejected holdings fetch left the data silently stale). `POST
  /api/invest/sync/<account>` now returns that reason (`error`, plus `kind` and `code`) instead of
  a bare "401 Client Error".
- **`refresh_tokens.py` no longer reports every failure as "Broker login needed".** Errors are
  classified (`brokers.classify_sync_error`): a rejected token still asks for a login, but a
  blocked fetch (e.g. Upstox static-IP rule, `UDAPI1221`) or an unreachable broker is pushed as
  "Broker sync failing" with the actual reason. `refresh_all()` keeps its old return value.
- **Concurrent saves of one file no longer fail intermittently on Windows.** Windows refuses a
  replace that overlaps another replace of the same file with `PermissionError` ("Access is
  denied"), and `atomic_write.py` retried every loser on the same fixed schedule, so twenty
  simultaneous writers could exhaust the retry budget (seen as a rare failure of
  `test_concurrent_writers_never_corrupt_file_or_leak_tmp_files` on the Windows CI jobs). The
  helper now serialises same-process replaces of one file and lengthens each backoff pause by a
  random 0-50% so writers that do collide drift apart; no pause is shorter than before. The
  previous file is still never truncated, the last replace still wins, and no `.tmp` file is left
  behind. Writes to `finances.json` were already serialised by the server's file lock; this
  mainly protects the unlocked `data/invest/*.json` writers. Writers in different processes
  are still separated by the retry loop alone.

### Added
- **Possible double-count warning on the Portfolio page** — a hand-typed row whose owner, asset
  class, invested and current value all match a synced broker row (within 15%) gets a
  "possible duplicate" chip and a page-level note. Warning only; nothing is merged or removed.
  Pure logic: `findLikelyDuplicateRows` in `public/finance-math.js`; the demo dataset includes
  one example row.
- **Live Investments dashboard** (`/invest`, 2026-07-16) — consolidates personal holdings across
  brokers: Zerodha Kite (multiple accounts), Upstox, Coin mutual funds, Wint Wealth
  bonds (multiple accounts), and smallcase into one view. Supports broker OAuth sync,
  holdings CSV import (`imports/`), Wint Wealth xlsx statement parsing, and manual
  holdings entry, plus buy/sell/keep signals, a bond cashflow view, and an IPO tracker
  (NSE/Upstox subscription sync, apply/skip recommendation, allotment tracking,
  past-IPO history from NSE + Chittorgarh). New CLI tools: `invest_cli.py`
  (import/ipo/wint subcommands), `daily_brief.py` (ntfy.sh phone pushes),
  `refresh_tokens.py` (unattended morning broker token refresh). New `investlib/`
  package, `invest_api.py`, and `config.py` at repo root; `.env.example` added for the
  optional broker/Gemini secrets. `--demo` now also seeds fake invest holdings.
- **Invest account owners + live values in Portfolio** — each invest account (Kite, Upstox,
  Coin, Wint Wealth, smallcase) now has an `owner`, settable via
  `POST /api/invest/accounts/<id>/owner` (auto-adds new names to `settings.persons`); a new
  `GET /api/invest/persons` returns the union of finances persons and account owners. Any
  owned account with holdings is injected into the finances Portfolio as a server-owned
  `liveSync` row (`investlib/bridge.py`) on every `GET`/`PUT /api/data` — a stale/tampered
  client value can never persist, since `PUT` always recomputes it from the invest store
  before saving. `--demo` seeds owners for the demo invest accounts so the bridge is
  visibly demoable.
- **Lending & borrowing ledger** — track money lent to / borrowed from each person, with
  fixed or percentage cashback, extra charges, cancellations, and a per-year breakdown.
- **"Hide values"** privacy toggle to mask all amounts on screen.
- Cross-process file lock around data writes — no two processes/instances can edit
  `finances.json` at once.
- Single-instance launch: re-running on the same data opens the running instance instead of
  starting a second writer (keyed on app id + data dir, so `--demo`/`--data-dir` still start
  separately).
- GitHub Actions CI running the full suite on Linux and Windows (Python 3.10 & 3.12),
  exercising both file-lock backends (`fcntl` and `msvcrt`).
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.gitattributes`, issue/PR templates.

### Changed
- **Dependency policy**: the core server and finance tracker remain Python-stdlib-only
  with nothing to install. The investments module's live broker/NSE/IPO sync is the one
  sanctioned exception — an *optional* extra (`pip install -r requirements-invest.txt`,
  ideally in a repo-local `.venv`); without it, holdings CSV/xlsx import, analysis, and
  manual IPO tracking still work stdlib-only, and the sync buttons just return a clear
  error instead of a crash.
- **Port handling is now deterministic**: the port comes from `ports.json` if present,
  otherwise from `--port`; the server no longer hunts for a free port and **fails clearly if
  the chosen port is already in use**.
- `--host 0.0.0.0` now prints an explicit warning that it exposes `finances.json` on the
  network (there is no authentication).

### Fixed
- **`loanState()` multi-prepayment bug** — two prepayments in the same month over-deducted the
  balance (it subtracted the cumulative prepay each iteration instead of the individual lump
  sum), skewing remaining months and total interest.
- Per-year ledger transaction count no longer includes cancelled rows (they are tracked
  separately).
- A corrupt `data/invest/<collection>.json` now raises a `ValueError` naming the exact file
  (and pointing at a possible `.bak`), instead of a bare `json.JSONDecodeError`, so the sync
  toast tells you which file to fix.
- Startup no longer crashes on Windows consoles using the legacy cp1252 code page (the `⚠`
  warning print raised `UnicodeEncodeError`); stdout/stderr are reconfigured to UTF-8 at
  startup.

[Unreleased]: https://github.com/ylnhari/family-finance-app/commits/main

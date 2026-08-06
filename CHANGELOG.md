# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and the project
uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- **Optional MyCard Benefits companion** — the existing Cards page now has a
  separate launcher and accessible setup dialog. The configured origin stays in
  browser-local storage, no card or finance data is sent, and a bundled guide
  opens when the companion is absent. The URL policy allows HTTP only on
  loopback or literal Tailscale IPv4 addresses and requires HTTPS elsewhere.
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

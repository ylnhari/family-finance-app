# Family Finance

[![CI](https://github.com/ylnhari/family-finance-app/actions/workflows/ci.yml/badge.svg)](https://github.com/ylnhari/family-finance-app/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A self-contained personal & family finance tracker that runs entirely on your own machine.
No accounts, no cloud, no dependencies — just Python and a browser.

## Quick start

```
git clone <this-repo-url>
cd family-finance-app
python server.py --port 8765   # start.bat / start.sh forward arguments: "start.bat --port 8765"
```

The app opens at http://127.0.0.1:8765. Press Ctrl+C to stop.
First run creates an empty `data/finances.json` — start adding your own numbers.

**Requirements:** Python 3.8+ (standard library only, nothing to `pip install`). The core
tracker and the investments dashboard's CSV/xlsx import both work with zero installs;
only *live* broker/NSE sync in the investments dashboard needs an optional extra — see
[Live Investments](#live-investments) below.

**Choosing the port:** the server binds a fixed port and **does not hunt for a free one**.
There is deliberately no default port: it's often pinned by broker OAuth redirect URLs
registered with Kite/Upstox, so it must be chosen consciously rather than assumed. The
precedence is `--port` > `FAMILY_FINANCE_PORT` env var > a sibling `ports.json` registry
entry > a clear error telling you to set one of the above. If the chosen port is already
in use it stops with a clear message (re-running while the app is already up just opens
the running instance). Other options: `--data-dir /path/to/data`, `--host`, `--no-browser`.

**Next:** try the demo dataset below, or jump straight to using it with your own data.

## Try the demo

A ready-made sample dataset (fake "Sharma Family") ships in `samples/` and exercises every
feature — multi-year salaries, a home loan with prepayments, gold with buy-price gains,
goals with market values, cards, and more.

```
python server.py --demo
```

This seeds a throwaway sandbox in `demo-data/` (gitignored) — **your real `data/` folder is
left completely untouched**, including backups and uploads. Reset the demo anytime by
deleting the `demo-data/` folder.

`--demo` also seeds a small **fake** investments portfolio (multiple accounts, made-up
owners) so you can explore [`/invest`](#live-investments) risk-free before touching a real
broker key — see the Live Investments section below.

## Tests

No frameworks — pure-math runs under Node's built-in runner, server/API under Python's `unittest`.

```
./test.sh        # or test.bat on Windows
```

Individually:
```
python -m unittest discover -s tests -p "test_*.py"        # server, persistence, --demo isolation, gemini parse logic
node --test tests/math.test.js tests/sample.test.js        # EMI/amortization/prepayment math + sample-data integrity
```

See [tests/TESTPLAN.md](tests/TESTPLAN.md) for the full coverage map.

## How your data is stored

| What | Where |
|---|---|
| All records (income, expenses, loans, cards…) | `data/finances.json` — one readable JSON file |
| Uploaded documents | `data/files/` |
| Automatic daily backups (last 14) | `data/backups/` |

**The app code contains zero personal information, and `data/` is git-ignored** — your
finances never end up in the repo. To move machines, copy the `data` folder (or use
Export / Import in Settings). To let someone else use the app, just give them this repo.

> ⚠️ `data/finances.json` is plain text and may hold card numbers and other sensitive
> details. Keep it on encrypted/private storage. Never remove `data/` from `.gitignore`.

## Features

- **Dashboard** — net worth, asset allocation, monthly cashflow, loan progress, upcoming goals
- **Income** — relational salary structure per family member: enter each component once, marked "Gross (counts in CTC)" or "CTC only"; **CTC = Gross + CTC-only items and In-Hand = Gross − Deductions are computed automatically** with input validation
- **Expenses** — sections with predefined category & location dropdowns ("add new for reuse" or "custom for this entry"); each section can track location, person, or just category & amount
- **Monthly Investments** — SIPs, PF, NPS, gold schemes; tracks whether each comes from in-hand, gross or CTC
- **Portfolio** — every holding with invested vs current value and returns, per owner, plus physical gold
- **Loans**
  - add a new loan (EMI auto-calculated) **or add a loan you're already paying midway**: enter just the EMI, months remaining and rate — the app derives the outstanding principal
  - full **amortization schedule** with paid-months marker and CSV download
  - **prepayment what-if**: see interest and months saved by paying extra each month
  - standalone EMI calculator, attach files/links to each loan, mark closed
- **Lending** — track money lent to or borrowed from people; stays outside net worth
- **Goals** — future purchases with down-payment/loan/EMI planning; one click converts a goal into a live loan
- **Cards** — wallet view of credit/debit cards with masked numbers (reveal on demand), benefits, lounge access, fees, activate/deactivate; filter by bank, owner, type (cards without a bank appear under "No Bank / Other")
- **Documents** — drag-and-drop file storage in your data folder, plus saved links
- **Settings** — currency & locale (works for any country), family members, export/import, full wipe
- **Backups** — automatic daily snapshot on the first save of each day (last 14 kept) **plus** manual "Backup now", one-click restore (with pre-restore safety snapshot) and per-backup download/delete in Settings
- **Dropdowns everywhere** — banks, card types, networks, instruments, asset classes, goal types, lenders, locations, people: pick a predefined value, save a new one for reuse, or type a one-time custom value

## Live Investments

A second dashboard, at **http://127.0.0.1:8765/invest**, consolidates a personal
investment portfolio alongside the finance tracker — same server, same stdlib-first
philosophy.

**Starting from zero:** a fresh `data/` folder means zero accounts — nothing is pre-seeded.
The first time you open `/invest` with no accounts yet, you'll see an onboarding screen
with three ways to add your first one: connect a broker (live OAuth sync), import a
broker statement (CSV/xlsx), or track a holding manually. Pick whichever fits; you can mix
all three across different accounts. See [`docs/BROKER-SETUP.md`](docs/BROKER-SETUP.md)
for the full broker-connection walkthrough.

- **Consolidated holdings** across brokers: Zerodha Kite (multiple accounts), Upstox,
  Coin mutual funds, Wint Wealth bonds (multiple accounts), smallcase — one net-worth
  view instead of five broker apps.
- **Broker OAuth sync** (Kite / Upstox "Login & sync" buttons — full setup walkthrough at
  [`docs/BROKER-SETUP.md`](docs/BROKER-SETUP.md)) or **CSV import** from
  the `imports/` folder — see below — for brokers you'd rather not connect live.
- **Wint Wealth xlsx statements** (no broker API) parsed directly from the "Holding
  Statement" / "Upcoming Cashflow Statement" reports the app itself exports.
- **Manual holdings** for anything with no export at all.
- **Buy/sell/keep signals** and a **bond cashflow view** — decision support, not
  advice; nothing places trades.
- **IPO tracker**: NSE/Upstox subscription-number sync, an apply/skip recommendation,
  allotment tracking, and past-IPO history (NSE + Chittorgarh).

**Optional extras — live sync only.** The dashboard, CSV/xlsx import, analysis, and
manual IPO tracking all work with zero installs. Only the *live* broker/NSE/IPO network
sync buttons need:

```
python -m venv .venv                        # repo-local virtualenv, recommended
.venv\Scripts\pip install -r requirements-invest.txt   # kiteconnect, upstox-python-sdk, requests, pyotp
```

`start.bat` auto-prefers `.venv\Scripts\python.exe` when present. Without the extras
installed, the sync buttons return a clear error — everything else keeps working.

**Secrets:** copy [`.env.example`](.env.example) to `.env` (gitignored). Every value
ships blank in the tracked example, and the app runs fine with none of them set.

- **Broker keys** are per-account credentials, not account definitions — create the
  account first in the `/invest` UI (or via the API), *then* add its `<ACCOUNT_ID>_API_KEY`
  / `_API_SECRET` (etc.) to `.env` using the id you chose. See
  [`docs/BROKER-SETUP.md`](docs/BROKER-SETUP.md) for the exact naming convention and the
  Kite/Upstox app-creation steps.
- **`GEMINI_API_KEY`** (optional) powers the payslip-extract and goal-price AI helpers.
  Get a free key at [aistudio.google.com](https://aistudio.google.com/apikey) (Google AI
  Studio) — leave it blank to keep those two endpoints off; everything else works
  regardless.

**Importing broker exports:** drop a downloaded holdings CSV (or Wint Wealth xlsx) into
`imports/` (gitignored except its own README) and run `invest_cli.py` — see
[`imports/README.md`](imports/README.md) for exactly where to get each broker's export.

**CLI tools** (repo root, all optional):
- `invest_cli.py` — `import` / `ipo` / `wint` subcommands to load holdings CSVs, manage
  the IPO tracker, and parse Wint Wealth xlsx statements from the command line, e.g.
  `python invest_cli.py import kite-1 imports/holdings.csv`.
- `daily_brief.py` — pushes an IPO-alert / Wint-refresh-reminder summary (never portfolio
  values) to your phone via [ntfy.sh](https://ntfy.sh) (needs `NTFY_TOPIC` in `.env`). Run
  it directly with `python daily_brief.py`, or schedule it — see
  [`docs/NOTIFICATIONS.md`](docs/NOTIFICATIONS.md) for what it sends, ntfy.sh setup, and
  both Windows Task Scheduler and cron examples.
- `refresh_tokens.py` — unattended morning broker token refresh, for anyone who'd
  rather not click "Login & sync" by hand (needs the optional `USER_ID` /
  `PASSWORD` / `TOTP_SECRET` values in `.env` — see
  [`docs/NOTIFICATIONS.md`](docs/NOTIFICATIONS.md) for the security tradeoffs of storing
  those before enabling it). Run directly with `python refresh_tokens.py`, or schedule it
  alongside `daily_brief.py`.

## Folder layout

```
family-finance-app/
├─ server.py                # the entire backend, Python stdlib only
├─ config.py                # investments module config: ports.json + .env loading
├─ invest_api.py            # /api/invest/* routes + broker OAuth callbacks
├─ invest_cli.py            # CLI: import / ipo / wint subcommands
├─ daily_brief.py           # optional: daily ntfy.sh phone push
├─ refresh_tokens.py        # optional: unattended morning broker token refresh
├─ investlib/               # investments package (brokers, analysis, ipo, portfolio…)
├─ requirements-invest.txt  # OPTIONAL extras for live broker/NSE/IPO sync
├─ .env.example             # copy to .env (gitignored) for broker/Gemini keys
├─ docs/
│  ├─ BROKER-SETUP.md       # Kite Connect + Upstox app creation, env mapping, first login
│  └─ NOTIFICATIONS.md      # ntfy.sh setup, daily_brief.py/refresh_tokens.py, scheduling
├─ start.bat                # Windows launcher (prefers .venv if present)
├─ start.sh                 # Mac/Linux launcher
├─ public/                  # the app: index.html, invest.html, style.css, app.js — no personal data
├─ imports/                 # drop broker CSV/xlsx exports here — gitignored except README
└─ data/                    # YOUR data — created on first run, git-ignored
   └─ invest/                # investment holdings/accounts/IPO tracker JSON
```

## Contributing

Contributions are welcome — please read [CONTRIBUTING.md](CONTRIBUTING.md) first (the big one:
**no new dependencies**, and every change ships with tests + demo data). See
[CHANGELOG.md](CHANGELOG.md) for what's changed and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
for community expectations.

## Security

Found a vulnerability? Please report it privately — see [SECURITY.md](SECURITY.md). In short:
this is a single-user local app, `data/` never leaves your machine, and the optional Gemini
endpoints are off unless you set an API key.

## License

MIT — see [LICENSE](LICENSE).

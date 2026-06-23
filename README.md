# Family Finance

[![CI](https://github.com/ylnhari/family-finance-app/actions/workflows/ci.yml/badge.svg)](https://github.com/ylnhari/family-finance-app/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A self-contained personal & family finance tracker that runs entirely on your own machine.
No accounts, no cloud, no dependencies — just Python and a browser.

## Quick start

```
git clone <this-repo-url>
cd family-finance-app
python server.py --port 8765   # or double-click start.bat (Windows) / ./start.sh (Mac & Linux)
```

The app opens at http://127.0.0.1:8765. Press Ctrl+C to stop.
First run creates an empty `data/finances.json` — start adding your own numbers.

**Requirements:** Python 3.8+ (standard library only, nothing to `pip install`).

**Choosing the port:** the server binds a fixed port and **does not hunt for a free one**.
It reads the port from a sibling `ports.json` registry if present, otherwise you pass
`--port`. If the chosen port is already in use it stops with a clear message (re-running while
the app is already up just opens the running instance). Other options: `--data-dir
/path/to/data`, `--host`, `--no-browser`.

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
- **Goals** — future purchases with down-payment/loan/EMI planning; one click converts a goal into a live loan
- **Cards** — wallet view of credit/debit cards with masked numbers (reveal on demand), benefits, lounge access, fees, activate/deactivate; filter by bank, owner, type (cards without a bank appear under "No Bank / Other")
- **Documents** — drag-and-drop file storage in your data folder, plus saved links
- **Settings** — currency & locale (works for any country), family members, export/import, full wipe
- **Backups** — automatic daily snapshot on the first save of each day (last 14 kept) **plus** manual "Backup now", one-click restore (with pre-restore safety snapshot) and per-backup download/delete in Settings
- **Dropdowns everywhere** — banks, card types, networks, instruments, asset classes, goal types, lenders, locations, people: pick a predefined value, save a new one for reuse, or type a one-time custom value

## Folder layout

```
family-finance-app/
├─ server.py        # the entire backend (~250 lines, Python stdlib only)
├─ start.bat        # Windows launcher
├─ start.sh         # Mac/Linux launcher
├─ public/          # the app (index.html, style.css, app.js) — no personal data
└─ data/            # YOUR data — created on first run, git-ignored
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

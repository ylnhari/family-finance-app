# Family Finance

A self-contained personal & family finance tracker that runs entirely on your own machine.
No accounts, no cloud, no dependencies — just Python and a browser.

## Quick start

```
git clone <this-repo-url>
cd family-finance-app
python server.py          # or double-click start.bat (Windows) / ./start.sh (Mac & Linux)
```

The app opens at http://127.0.0.1:8765. Press Ctrl+C to stop.
First run creates an empty `data/finances.json` — start adding your own numbers.

**Requirements:** Python 3.8+ (standard library only, nothing to `pip install`).

Options: `--port 9000`, `--data-dir /path/to/data`, `--no-browser`

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
- **Income** — full salary structure per family member: CTC components, gross, deductions, in-hand (click any amount to edit)
- **Expenses** — grouped recurring expenses by category & location with live totals
- **Monthly Investments** — SIPs, PF, NPS, gold schemes; tracks whether each comes from in-hand, gross or CTC
- **Portfolio** — every holding with invested vs current value and returns, per owner, plus physical gold
- **Loans**
  - add a new loan (EMI auto-calculated) **or add a loan you're already paying midway**: enter just the EMI, months remaining and rate — the app derives the outstanding principal
  - full **amortization schedule** with paid-months marker and CSV download
  - **prepayment what-if**: see interest and months saved by paying extra each month
  - standalone EMI calculator, attach files/links to each loan, mark closed
- **Goals** — future purchases with down-payment/loan/EMI planning; one click converts a goal into a live loan
- **Cards** — wallet view of credit/debit cards with masked numbers (reveal on demand), benefits, lounge access, fees, activate/deactivate
- **Documents** — drag-and-drop file storage in your data folder, plus saved links
- **Settings** — currency & locale (works for any country), family members, export/import, full wipe

## Folder layout

```
family-finance-app/
├─ server.py        # the entire backend (~250 lines, Python stdlib only)
├─ start.bat        # Windows launcher
├─ start.sh         # Mac/Linux launcher
├─ public/          # the app (index.html, style.css, app.js) — no personal data
└─ data/            # YOUR data — created on first run, git-ignored
```

## License

MIT — see [LICENSE](LICENSE).

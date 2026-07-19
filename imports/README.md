# imports/

Drop broker holdings CSV exports here, then run
`python invest_cli.py import <account-id> <filename>`.

Everything in this folder except this README is gitignored — real holdings
never reach git.

Where to get the CSVs:
- **Zerodha Kite**: console.zerodha.com → Portfolio → Holdings → download icon
- **Upstox**: account → Reports → Holdings
- **Coin**: coin.zerodha.com → Portfolio → export (or Console shows MF too)
- **Wint Wealth**: no plain CSV, but the app's "Reports" section has a
  "Holding Statement" xlsx (and an "Upcoming Cashflow Statement" xlsx) —
  drop both here and run
  `python invest_cli.py wint <account-id> <holding.xlsx> --cashflow <cashflow.xlsx>`.
  Falls back to the manual-holdings API for brokers with no export at all.

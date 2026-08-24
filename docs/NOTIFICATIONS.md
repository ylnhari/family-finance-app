# Notifications & unattended token refresh

Two optional CLI scripts push phone notifications via [ntfy.sh](https://ntfy.sh) — a free,
no-signup push-notification service. Both are entirely optional; the app works fully
without either.

## What gets sent, and when

**`daily_brief.py`** — meant to run once or twice a day (morning + early afternoon is a
reasonable default; adjust to taste):
- Refreshes IPO subscription numbers (tries the Upstox IPO API first, falls back to NSE
  scraping), then pushes an **IPO alert** listing any tracked IPO with an APPLY or CAUTION
  call that's still open, with days-to-close.
- IPO APPLY/CAUTION alerts are generated only on regular NSE Capital Market bidding days. The
  daily task may still run on Saturdays, Sundays, or exchange holidays, but the IPO path exits
  before refreshing IPO data or sending an IPO alert; separate allotment and Wint reminders keep
  their existing behavior. The next open-day run checks IPOs again. The local calendar is kept
  in `investlib/market_calendar.py` and must be refreshed annually with official NSE holiday
  lists and amendments. This gate is for live bidding reminders; it does not change the
  dashboard's calendar-day countdown or imply that this app places an order.
- Pushes a separate **allotment-day reminder** for any IPO you applied to, on the day
  allotment finalizes.
- Pushes a separate **Wint Wealth staleness reminder** (at most once every 7 days per
  account) nudging you to re-download and re-import your Wint holding/cashflow
  statements once they've gone stale.
- **Deliberately never includes portfolio figures or holding values** — IPO alerts carry
  only public NSE/Upstox data (names, subscription multiples, the apply/skip call); the
  Wint reminder carries only "it's been N days," never an amount. All real numbers stay
  on the local dashboard at `/invest`.
- When a topic is configured, a non-2xx ntfy response or transport failure makes the
  script fail with a topic-safe error. This prevents Task Scheduler from reporting a
  false success when ntfy rejects the push; a 2xx response still confirms only that ntfy
  accepted the request, not that a phone displayed it.
- If `INVESTMENTS_NTFY_TOPIC` isn't set, it just prints what it would have sent and exits — safe to
  run with no config at all.

**`refresh_tokens.py`** — meant to run once, early each morning, after both brokers'
nightly token expiry:
- Logs in **fully headless** to every configured Kite account using stored
  `_USER_ID`/`_PASSWORD`/`_TOTP_SECRET` credentials, then syncs its holdings.
- Upstox has no reliable headless login path, so an Upstox account (or any Kite account
  whose headless login failed) instead gets a single ntfy push titled "Broker login
  needed": "Log in at the dashboard for: `<account ids>`" — you find out *before* you
  trust stale numbers, rather than silently working from yesterday's data.
- Same privacy rule as `daily_brief.py`: the push names only which account needs
  attention, never a balance or holding.

Run either by hand any time to see what they'd send:

```
python daily_brief.py
python refresh_tokens.py
```

## Setting up ntfy.sh

1. **Pick a topic name.** Per ntfy's own docs, "topic names are public, so it's wise to
   choose something that cannot be guessed easily" — treat the topic name as a password,
   not a label. A random string (e.g. a UUID fragment or a long passphrase-like slug)
   works well; avoid anything guessable like your name or "family-finance."
2. **Subscribe on your phone.** Install the ntfy app —
   [Google Play / F-Droid (Android)](https://ntfy.sh) or the App Store (iOS), both linked
   from [ntfy.sh](https://ntfy.sh) — and add your topic inside the app. No account or
   sign-up needed.
   - Or subscribe in a browser at `https://ntfy.sh/<your-topic>` — works, but a
     browser tab has to stay open to receive live pushes; the phone app is the practical
     choice for a "silent until something's actionable" daily brief.
3. **Set the investments-only env var.** In `.env`:
   ```
   INVESTMENTS_NTFY_TOPIC=your-hard-to-guess-topic-here
   ```
   Do not use the generic `NTFY_TOPIC`; another automation may own that variable.
4. Restart the server (or just run the scripts directly — they read `.env` themselves via
   `config.py`, independent of the running server).

That's it — no API key, no account, nothing else to configure. Leave `INVESTMENTS_NTFY_TOPIC` blank
to keep both scripts print-only (they print an `INVESTMENTS_NTFY_TOPIC not set; printing only` line
and skip the push).

## Missing the optional extras?

Both scripts also import `requests` optionally (`requirements-invest.txt`). If it isn't
installed, they don't crash — they print a `requests not installed (pip install -r
requirements-invest.txt); printing only` line and skip the push, same as leaving
`INVESTMENTS_NTFY_TOPIC` unset. Install the extras (see `docs/BROKER-SETUP.md` → "Before you start")
if you want the actual phone push, not just the console line.

## The TOTP secret — where it comes from, and the tradeoff

`refresh_tokens.py`'s headless Kite login needs three extra values per account beyond the
API key/secret: `_USER_ID` (your Zerodha client ID), `_PASSWORD`, and `_TOTP_SECRET`.

`_TOTP_SECRET` is **not** a live 6-digit code — it's the **base32 seed** behind your
external-2FA authenticator QR code, found in Kite's own console at
**Console → Settings → Password & security → External 2FA / TOTP**. If you've already
scanned that QR into an authenticator app, the seed is the same string that QR encodes
(some authenticator apps let you export/reveal it; otherwise you'd re-generate the QR
from Kite's console and read the seed from it before scanning).

**Security caveat — read before setting these:** `_USER_ID` + `_PASSWORD` +
`_TOTP_SECRET` together are functionally your **entire broker login**, including the
ability to bypass 2FA. Unlike an OAuth access token (which expires nightly and is scoped
to the Kite Connect API), these three values can log in to the full Zerodha web/app login
with no expiry and no scope restriction. Consequences of that file leaking are
correspondingly worse than a leaked API key.

- These values live **only** in your local, gitignored `.env` — never commit them, never
  paste them into an issue/PR/chat, never store them in a synced note or password manager
  entry that isn't already protecting equivalent secrets.
- If your machine's disk isn't encrypted, or `.env` might get swept into a cloud-synced
  folder (Dropbox/OneDrive/iCloud Drive) by accident, treat that as equivalent to storing
  your broker password in plaintext in that location — because it is.
- This whole trio is **optional**. The default, safer path is clicking "Login & sync" by
  hand each morning (needs only `_API_KEY`/`_API_SECRET`, which are scoped and revocable
  broker-side). Only add the TOTP trio if the unattended convenience is worth that
  tradeoff to you.
- If you ever suspect these values leaked, treat it as a compromised broker login —
  change your Zerodha password and rotate your TOTP secret (regenerate the QR in Kite's
  console), not just delete the `.env` line.

Upstox has no equivalent headless-password path in this app; its unattended option is the
long-lived, **read-only** Analytics Token described in `docs/BROKER-SETUP.md`, which is a
meaningfully smaller blast radius than a Kite password + TOTP seed since it can't place
trades or change account settings.

## Scheduling — Windows Task Scheduler

1. Open **Task Scheduler** → **Create Task…** (not "Basic Task," so you get the full
   options below).
2. **General** tab: give it a name (e.g. "Family Finance — daily brief"). Under
   "Security options," consider "Run whether user is logged on or not" if you want it to
   fire even when locked — you'll be prompted for your Windows account password once to
   save the task.
3. **Triggers** tab → **New…** → Daily, at a time of your choosing (a common choice is
   shortly after your broker's nightly token reset — see `docs/BROKER-SETUP.md` for
   where to look that up — and again in the afternoon if you want a second IPO check).
4. **Actions** tab → **New…** → "Start a program":
   - **Program/script:** the full path to your Python interpreter. If you installed the
     optional extras into a `.venv`, use that venv's `python.exe`
     (`<repo path>\.venv\Scripts\python.exe`) — a scheduled task does **not** inherit a
     shell's venv activation, so pointing it at a bare `python`/`py` on PATH will silently
     run without the extras and the script will report them missing.
   - **Add arguments:** `daily_brief.py` (a second task with `refresh_tokens.py` for the
     token-refresh script).
   - **Start in:** the repo root (so relative paths like `.env` resolve correctly).
5. **Conditions**/**Settings** tabs: defaults are usually fine; consider unchecking "Stop
   the task if it runs longer than" only if your network is slow, and "Start the task
   only if the computer is on AC power" if this is a laptop you want it to run on battery.
6. Save. Test it once via **Run** in the Task Scheduler list and confirm you get the
   phone push (or the "nothing actionable today" console line if there's nothing to send).

Repeat for `refresh_tokens.py` as a second task if you want unattended token refresh too.

## Scheduling — cron (Mac/Linux)

```cron
# crontab -e
# Daily brief: adjust the time to suit your timezone / broker reset time.
0 7 * * * cd /path/to/family-finance-app && ./.venv/bin/python daily_brief.py >> /path/to/family-finance-app/logs/daily_brief.log 2>&1

# Unattended token refresh, same idea:
0 7 * * * cd /path/to/family-finance-app && ./.venv/bin/python refresh_tokens.py >> /path/to/family-finance-app/logs/refresh_tokens.log 2>&1
```

Notes:
- Use the **`.venv`** interpreter path explicitly (`./.venv/bin/python`), not a bare
  `python3` — `start.sh` doesn't auto-activate a venv either (see `docs/BROKER-SETUP.md`),
  and cron doesn't inherit your shell's venv activation, same reasoning.
- `cd` into the repo first (or use absolute paths throughout) so `.env` and `data/` are
  found relative to the repo root.
- Redirecting output to a log file is optional but makes silent cron failures debuggable;
  create the `logs/` directory yourself (or drop the redirect and rely on cron's own mail
  delivery, if configured on your system).
- `launchd` is the native macOS alternative to cron if you prefer a `.plist`-based
  scheduler — the same program/arguments/working-directory mapping applies, just in
  `launchd`'s XML format instead of a crontab line.

## Safe to try with zero configuration

Both scripts check what's configured before doing anything broker-specific: with no
`INVESTMENTS_NTFY_TOPIC`, they print instead of pushing; with no broker credentials configured for an
account, `refresh_tokens.py` just skips it. You can schedule both scripts immediately
after cloning, before setting up any broker or notification config, to see exactly what
they'd do — nothing will error out from missing optional config.

# Connecting a broker (Kite Connect / Upstox)

This walks through connecting a real brokerage account (Zerodha Kite or Upstox) to the
investments dashboard at `/invest`, so holdings sync automatically instead of being
imported by hand. **None of this is required** — CSV/xlsx import and manual tracking work
with zero setup and zero installs; this guide is only for the "Login & sync" live path.

## Before you start

- The core app and CSV/xlsx import need nothing extra. Live broker sync needs the
  optional extras in `requirements-invest.txt` (`kiteconnect`, `upstox-python-sdk`,
  `requests`, `pyotp`):

  ```
  python -m venv .venv
  .venv\Scripts\pip install -r requirements-invest.txt      # Windows
  # .venv/bin/pip install -r requirements-invest.txt         # Mac/Linux
  ```

  `start.bat` auto-prefers `.venv\Scripts\python.exe` when it exists. `start.sh`
  currently just runs `python3 server.py` — it does **not** auto-detect a venv — so on
  Mac/Linux either activate the venv first (`source .venv/bin/activate && ./start.sh`)
  or run the venv's interpreter directly (`.venv/bin/python server.py`). Without the
  extras installed, the "Login & sync" buttons return a clear error instead of
  crashing — everything else keeps working.

- Know which port your app serves on. Default is **8765**; you may have set a different
  one via `--port` or a `ports.json` entry. Every redirect URL below substitutes
  `<your-app-port>` for that number.

- Coin (Zerodha's mutual-fund platform) isn't its own broker connection — it rides along
  on whichever Kite account you name in `COIN_SOURCE_ACCOUNT` (see step 4). Wint Wealth
  and smallcase have no OAuth login at all (xlsx import / manual, respectively).

## Step 1 — create the account in the app

Open `/invest`. On a fresh install this page starts with **zero accounts** and shows an
onboarding screen ("Set up your investments") with three paths: **Connect a broker**,
**Import statements**, or **Track manually**. Pick **Connect a broker**, or scroll to the
"Add account" form directly:

1. Broker type: `kite` or `upstox` (also available: `coin`, `wint`, `smallcase`, `manual`
   for the non-OAuth account types).
2. Account id: a short slug, e.g. `kite-1`, `kite-2`, `upstox-1` — these are just
   examples, not reserved names; pick anything id-safe (lowercase letters, digits,
   hyphens — the app suggests one for you based on the broker type).
3. Label: a human-readable name for the account (e.g. "Kite — primary").
4. Owner: free text, with suggestions drawn from your finance tracker's family members
   and any owners you've already used on other invest accounts.

Once you save a broker-type account, the app shows you:
- the **exact env var names** it now expects (see step 4 below),
- the **exact redirect URL** to register with the broker (computed from your browser's
  current origin + `/auth/<broker>/<account-id>/callback` — see the callout under
  Troubleshooting if you browse the app via a hostname other than `127.0.0.1`), and
- a link back to this guide.

Do this step *before* creating the developer app below — you need the account id decided
first, because it's baked into the redirect URL you register with Kite/Upstox.

## Step 2 — create the Kite Connect app (if using Kite)

Vendor pages: [developers.kite.trade](https://developers.kite.trade) (dashboard),
[Zerodha's own pricing article](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/what-are-the-charges-for-kite-apis).

**Cost, per Zerodha's own support article:** Kite Connect has a **free "Personal"** tier —
order/GTT/alert management and portfolio/holdings access, which is all this app needs —
and a separate **paid "Connect" tier at ₹500/month per app** that additionally unlocks
live/historical market-data endpoints this app does not call. Create the **free Personal**
app; you do not need the paid tier just to sync holdings.

1. Sign up / log in at `developers.kite.trade` with the Zerodha login the app will trade
   for — one Kite Connect app per Zerodha account/login.
2. Go to "My Apps" → "Create new app."
3. Fill in the form — app name, your Zerodha client ID, a short description, and the
   **Redirect URL**. This must be exactly:

   ```
   http://127.0.0.1:<your-app-port>/auth/kite/<account-id>/callback
   ```

   using the same `<account-id>` you chose in step 1 (e.g.
   `http://127.0.0.1:8765/auth/kite/kite-1/callback`). Kite Connect registers one redirect
   URL per app and matches it exactly — a trailing slash or wrong port will fail the
   callback. **Postback URL** can be left blank (this app doesn't place orders, so there
   are no order-status postbacks to receive).
4. Submit. You'll get an **API key** and **API secret** — copy both.
5. *Verify at signup:* whether newly created apps activate immediately or need a short
   review; Zerodha's own docs weren't explicit on activation delay at the time of writing.

Repeat for a second Kite account if you have one — each gets its own app, its own
API key/secret, and its own account id (`kite-2`, etc.).

## Step 3 — create the Upstox app (if using Upstox)

Vendor pages: [account.upstox.com/developer/apps](https://account.upstox.com/developer/apps)
(dashboard), [Upstox developer API docs](https://upstox.com/developer/api-documentation).

**Cost:** Upstox's own developer docs describe API access and app registration as free,
including the read-only Portfolio/Holdings endpoints this app uses; Upstox has run
promotional per-order trading pricing for *placing orders through the API* (not relevant
here — this app never places orders, only reads holdings). *Verify current terms at
signup* since Upstox's promotional pricing pages carry date-limited language.

1. Sign in at `account.upstox.com/developer/apps` and create a new app.
2. Set the **Redirect URI** to exactly:

   ```
   http://127.0.0.1:<your-app-port>/auth/upstox/<account-id>/callback
   ```

   e.g. `http://127.0.0.1:8765/auth/upstox/upstox-1/callback`. Per Upstox's own OAuth
   docs, the `redirect_uri` sent during login **must exactly match** what's registered on
   the app — mismatches fail the authorization step. **Use `127.0.0.1` literally** (not
   `localhost` or a LAN IP) — the app always builds the redirect URL it sends to Upstox
   from `127.0.0.1`, regardless of which hostname you happen to be browsing the dashboard
   from (see the Troubleshooting callout below).
3. Save. You'll get a **client ID (API key)** and **client secret (API secret)**.
4. Optional — skip the daily login entirely: Upstox's developer docs describe a
   long-lived (about 1 year), **read-only Analytics Token** you generate once from
   your app's "Analytics" tab. Using it to read holdings/portfolio data requires a
   **Static IP** registered on the app (Developer Apps → Static IP = your connection's
   public IP; per Upstox's docs, static IPs can only be changed once a week and market-data
   endpoints work without one). If your home IP isn't static, skip this and use the
   ordinary "Login & sync" button each morning instead.

## Step 4 — map credentials into `.env`

Copy `.env.example` to `.env` (gitignored) if you haven't already. The env var naming
convention is derived from the account id you chose in step 1: uppercase it and turn
hyphens into underscores, then append a suffix:

| Suffix | Required for | Example (account id `kite-1`) |
|---|---|---|
| `_API_KEY` | Kite, Upstox | `KITE_1_API_KEY` |
| `_API_SECRET` | Kite, Upstox | `KITE_1_API_SECRET` |
| `_USER_ID` | Kite headless refresh only | `KITE_1_USER_ID` |
| `_PASSWORD` | Kite headless refresh only | `KITE_1_PASSWORD` |
| `_TOTP_SECRET` | Kite headless refresh only | `KITE_1_TOTP_SECRET` |
| `_ANALYTICS_TOKEN` | Upstox, optional (skips daily login) | `UPSTOX_1_ANALYTICS_TOKEN` |

`_API_KEY`/`_API_SECRET` are the only two required to use the "Login & sync" button by
hand each morning. The `_USER_ID`/`_PASSWORD`/`_TOTP_SECRET` trio and
`_ANALYTICS_TOKEN` are both entirely optional — see `docs/NOTIFICATIONS.md` for the
unattended-refresh path and its security tradeoffs before filling those in.

If you use Coin (Zerodha mutual funds), set `COIN_SOURCE_ACCOUNT` to the id of whichever
Kite account owns your Coin folio (defaults to `kite-1`). If that id doesn't match an
account you've actually created, the app should tell you clearly rather than silently
doing nothing — if it doesn't, that's a bug, not expected behavior.

Restart the server after editing `.env` (it's loaded once at startup).

## Step 5 — first login

1. Open `/invest`. Your new account now appears in the **Broker sync** table with a
   "Credentials" badge — it should read **"configured"** now that `.env` has the
   key/secret. If it still says **"add keys to .env,"** double-check the env var names
   against step 4 and that you restarted the server.
2. Click **"→ Login & sync"** next to the account.
3. You're redirected to the broker's own login page (kite.zerodha.com or
   Upstox's login). Log in normally.
   - Kite: after your password, you'll be prompted for a **TOTP 2FA code** (the live
     6-digit code from your authenticator app) — this is separate from the `TOTP_SECRET`
     env var, which is only used by the *unattended* refresh script, not this manual flow.
4. On success, the broker redirects back to
   `http://127.0.0.1:<your-app-port>/auth/<kite|upstox>/<account-id>/callback`, which
   exchanges the code for an access token, stores it, syncs your holdings immediately,
   and sends you back to `/invest`.
5. Tokens expire nightly for both brokers — you'll need to click "Login & sync" again
   each morning, or set up the unattended refresh described in `docs/NOTIFICATIONS.md`.

## Troubleshooting

- **"Invalid redirect_uri" / broker rejects the callback** — the redirect URL registered
  on the broker's app must match **character-for-character** what the account actually
  uses: same scheme (`http://`, not `https://`), same host (`127.0.0.1`), same port
  (your actual running port, not the default if you changed it), same account id, and no
  trailing slash. If you changed `--port` after registering the app, update the app's
  redirect URL to match — the app doesn't try multiple ports.
- **The redirect URL the app shows you doesn't match what you registered** — the on-screen
  hint in the "Add account" panel is computed from your browser's current address bar
  (`window.location.origin`). If you're browsing the dashboard via `localhost` or a LAN
  IP instead of `127.0.0.1`, that hint will show the wrong host. For **Upstox**, the app
  always sends `127.0.0.1` as the actual `redirect_uri` when it talks to Upstox (it's
  hardcoded, same as this guide) regardless of what URL is in your browser bar — so
  always register `http://127.0.0.1:<port>/...` on Upstox's side, not whatever hostname
  you happened to be viewing `/invest` from. For **Kite**, there's no such mismatch risk
  since Kite doesn't take a redirect URL as a login parameter at all — whatever you
  register in the Kite Connect console is authoritative.
- **Token expired / holdings look stale** — both brokers invalidate access tokens
  overnight. Click "Login & sync" again; there's nothing wrong, this is expected daily
  behavior. `docs/NOTIFICATIONS.md` covers automating this.
- **"Live broker sync needs the optional dependencies"** — the extras
  (`requirements-invest.txt`) aren't installed, or you're running a plain
  `python`/`python.exe` instead of the `.venv` one. Re-run the venv install command from
  "Before you start," and confirm you're actually launching with
  `.venv\Scripts\python.exe` / `.venv/bin/python` (see the `start.sh` note above — it
  does not do this for you automatically).
- **Account doesn't appear in the Broker sync table at all** — it needs to exist in the
  account registry first (step 1) before the app looks for its env vars; creating a
  `.env` entry alone for an id you never added in the UI won't surface anything.
- **Upstox Analytics Token returns no holdings** — that token needs a Static IP
  registered on the app (see step 3); without one, use the ordinary daily login instead.
- **`COIN_SOURCE_ACCOUNT` set to an account id that doesn't exist** — should degrade with
  a clear message rather than fail silently; if you hit a silent no-op or crash instead,
  file it as a bug.

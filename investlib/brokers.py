"""Broker API sync: Zerodha Kite Connect (free Personal plan) and Upstox.

Daily-token reality: both brokers expire access tokens every night, so each
morning the dashboard shows a "login" button per account; clicking it runs
the OAuth/request-token dance through the browser and lands back on our
callback route, which stores the token in data/tokens.json and syncs
holdings immediately.

Per-account credentials come from .env, prefixed by the account id:
  KITE_1_API_KEY / KITE_1_API_SECRET       (app at developers.kite.trade,
  KITE_2_API_KEY / KITE_2_API_SECRET        redirect URL must be
                                            http://127.0.0.1:<port>/auth/kite/<id>/callback)
  UPSTOX_1_API_KEY / UPSTOX_1_API_SECRET   (app at account.upstox.com/developer/apps,
                                            redirect http://127.0.0.1:<port>/auth/upstox/<id>/callback)

Coin mutual funds ride along with a Kite session (same Zerodha login);
COIN_SOURCE_ACCOUNT says which Kite account owns the Coin folio.
"""

import os
from datetime import date

try:
    import requests  # optional — live broker sync only (requirements-invest.txt)
except ImportError:
    class _MissingRequests:
        def __getattr__(self, name):
            raise RuntimeError(
                "Live broker sync needs the optional dependencies: "
                "pip install -r requirements-invest.txt")
    requests = _MissingRequests()

import config
from . import portfolio, store

COIN_SOURCE_ACCOUNT = os.environ.get("COIN_SOURCE_ACCOUNT", "kite-1")


def api_accounts() -> list:
    """The accounts we can log in to — every kite/upstox account in the
    registry, as (id, kind). Data-driven (no hardcoded personal list): a fresh
    install returns []. Coin rides along on its Kite account, so it isn't here.
    token_status and the unattended refresh (refresh_tokens.py) iterate this."""
    return [(a["id"], a["broker_type"]) for a in store.accounts()
            if a.get("broker_type") in ("kite", "upstox")]


def _broker_type(account_id: str) -> str | None:
    """Broker type for an account id from the registry (None if unknown)."""
    for a in store.accounts():
        if a.get("id") == account_id:
            return a.get("broker_type") or None
    return None


def _coin_target() -> str | None:
    """The account id that holds Coin (Zerodha MF) holdings, if one exists."""
    return next((a["id"] for a in store.accounts()
                 if a.get("broker_type") == "coin"), None)


def coin_source_issue() -> str | None:
    """A clear message when Coin is set up but COIN_SOURCE_ACCOUNT is misconfigured.

    Coin MF holdings ride along on a Kite login, so COIN_SOURCE_ACCOUNT must name
    a real kite account. Returns None when there's nothing to warn about (no coin
    account, or a valid source)."""
    if _coin_target() is None:
        return None
    src = _broker_type(COIN_SOURCE_ACCOUNT)
    if src is None:
        return (f"COIN_SOURCE_ACCOUNT names '{COIN_SOURCE_ACCOUNT}', which is not an "
                f"account — Coin holdings won't sync until it points at a real Kite account.")
    if src != "kite":
        return (f"COIN_SOURCE_ACCOUNT '{COIN_SOURCE_ACCOUNT}' is not a Kite account — "
                f"Coin holdings ride a Kite login, so pick a kite-* account.")
    return None


def _env(account_id: str, suffix: str) -> str:
    key = f"{account_id.upper().replace('-', '_')}_{suffix}"
    value = os.environ.get(key)
    if not value:
        raise ValueError(f"missing {key} in .env")
    return value


def _env_opt(account_id: str, suffix: str) -> str | None:
    """Like _env but returns None when unset, for optional keys."""
    return os.environ.get(f"{account_id.upper().replace('-', '_')}_{suffix}") or None


def _redirect_url(kind: str, account_id: str) -> str:
    return f"http://{config.HOST}:{config.port()}/auth/{kind}/{account_id}/callback"


# ---- token store -----------------------------------------------------------

def _tokens() -> dict:
    return store.load("tokens", default={})


def save_token(account_id: str, access_token: str) -> None:
    tokens = _tokens()
    tokens[account_id] = {"access_token": access_token, "generated": date.today().isoformat()}
    store.save("tokens", tokens)


def token_for(account_id: str) -> str | None:
    """Access token if generated today (both brokers expire tokens nightly)."""
    entry = _tokens().get(account_id)
    if entry and entry.get("generated") == date.today().isoformat():
        return entry["access_token"]
    return None


def token_status() -> dict:
    """Per API account: credentials configured? token fresh today?"""
    out = {}
    for acct_id, kind in api_accounts():
        try:
            _env(acct_id, "API_KEY")
            configured = True
        except ValueError:
            configured = False
        # a long-lived Upstox Analytics Token counts as always-fresh (no daily login)
        analytics = kind == "upstox" and _env_opt(acct_id, "ANALYTICS_TOKEN") is not None
        out[acct_id] = {"kind": kind, "configured": configured,
                        "token_fresh": analytics or token_for(acct_id) is not None,
                        "long_lived": analytics}
    return out


# ---- Zerodha Kite (also brings in Coin MF holdings) ------------------------

def _kite(account_id: str):
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise RuntimeError(
            "Live Kite sync needs the optional dependencies: "
            "pip install -r requirements-invest.txt")
    return KiteConnect(api_key=_env(account_id, "API_KEY"))


def kite_login_url(account_id: str) -> str:
    return _kite(account_id).login_url()


def kite_exchange(account_id: str, request_token: str) -> None:
    kite = _kite(account_id)
    session = kite.generate_session(request_token, api_secret=_env(account_id, "API_SECRET"))
    save_token(account_id, session["access_token"])


KITE_WEB = "https://kite.zerodha.com"


def kite_headless_login(account_id: str) -> None:
    """Log in without a browser, for the unattended morning refresh.

    Kite's web login is a two-step POST (password, then TOTP 2FA); once the
    session is authenticated, hitting the Kite Connect login URL redirects
    straight to our callback with a ?request_token=..., which we feed to the
    normal exchange. Needs, per account, in .env (beyond API_KEY/API_SECRET):
    KITE_x_USER_ID, KITE_x_PASSWORD, KITE_x_TOTP_SECRET (the base32 seed
    behind the authenticator QR, not a live 6-digit code)."""
    try:
        import pyotp
    except ImportError:
        raise RuntimeError(
            "Headless Kite login needs the optional dependencies: "
            "pip install -r requirements-invest.txt")
    from urllib.parse import urlparse, parse_qs

    user_id = _env(account_id, "USER_ID")
    session = requests.Session()

    resp = session.post(f"{KITE_WEB}/api/login",
                        data={"user_id": user_id, "password": _env(account_id, "PASSWORD")},
                        timeout=30)
    resp.raise_for_status()
    request_id = resp.json()["data"]["request_id"]

    resp = session.post(f"{KITE_WEB}/api/twofa", data={
        "user_id": user_id,
        "request_id": request_id,
        "twofa_value": pyotp.TOTP(_env(account_id, "TOTP_SECRET")).now(),
        "twofa_type": "totp",
    }, timeout=30)
    resp.raise_for_status()

    # Authenticated now: follow the Connect login redirects (same-domain hops)
    # until one carries the request_token, then exchange it for an access token.
    url = f"{KITE_WEB}/connect/login?api_key={_env(account_id, 'API_KEY')}&v=3"
    for _ in range(10):
        resp = session.get(url, allow_redirects=False, timeout=30)
        location = resp.headers.get("Location", "")
        target = location or resp.url
        if "request_token=" in target:
            token = parse_qs(urlparse(target).query)["request_token"][0]
            kite_exchange(account_id, token)
            return
        if resp.is_redirect and location:
            url = location if location.startswith("http") else f"{KITE_WEB}{location}"
            continue
        break
    raise ValueError(f"{account_id}: Kite headless login produced no request_token "
                     f"(wrong password/TOTP, or Kite changed the login flow?)")


def kite_sync(account_id: str) -> dict:
    """Pull stock holdings (and Coin MF holdings if this account owns Coin)."""
    token = token_for(account_id)
    if not token:
        raise ValueError(f"{account_id}: no fresh token — login first")
    kite = _kite(account_id)
    kite.set_access_token(token)

    rows = [{
        "symbol": h["tradingsymbol"], "isin": h.get("isin", ""),
        "quantity": h["quantity"] + h.get("t1_quantity", 0),
        "avg_price": h["average_price"], "last_price": h["last_price"],
    } for h in kite.holdings()]
    result = {account_id: len(portfolio.set_manual_holdings(account_id, rows)["rows"])}
    _mark_source(account_id, "kite api")

    # Coin MF holdings ride this Kite session when this account is the configured
    # source AND a coin account exists to hold them. Both guards keep a missing/
    # misconfigured coin account from crashing the stock sync.
    coin_target = _coin_target()
    if account_id == COIN_SOURCE_ACCOUNT and coin_target:
        mf_rows = [{
            "symbol": h.get("fund", h.get("tradingsymbol", "")),
            "isin": h.get("tradingsymbol", ""),
            "quantity": h["quantity"], "avg_price": h["average_price"],
            "last_price": h["last_price"],
        } for h in kite.mf_holdings()]
        result[coin_target] = len(
            portfolio.set_manual_holdings(coin_target, mf_rows)["rows"])
        _mark_source(coin_target, "kite api (coin)")
    return result


# ---- Upstox ----------------------------------------------------------------

UPSTOX_AUTH = "https://api.upstox.com/v2/login/authorization"
UPSTOX_API = "https://api.upstox.com/v2"


def upstox_token(account_id: str) -> str | None:
    """Prefer a long-lived Analytics Token (UPSTOX_x_ANALYTICS_TOKEN, generated
    once from Upstox's developer portal, valid ~1 year, read-only) so Upstox
    needs no daily login at all. Falls back to today's OAuth token from a manual
    dashboard login. NOTE: reading holdings with the Analytics Token requires a
    Static IP configured on the Upstox app (see README)."""
    return _env_opt(account_id, "ANALYTICS_TOKEN") or token_for(account_id)


def upstox_login_url(account_id: str) -> str:
    return (f"{UPSTOX_AUTH}/dialog?response_type=code"
            f"&client_id={_env(account_id, 'API_KEY')}"
            f"&redirect_uri={_redirect_url('upstox', account_id)}")


def upstox_exchange(account_id: str, code: str) -> None:
    resp = requests.post(f"{UPSTOX_AUTH}/token", data={
        "code": code,
        "client_id": _env(account_id, "API_KEY"),
        "client_secret": _env(account_id, "API_SECRET"),
        "redirect_uri": _redirect_url("upstox", account_id),
        "grant_type": "authorization_code",
    }, timeout=30)
    resp.raise_for_status()
    save_token(account_id, resp.json()["access_token"])


def upstox_sync(account_id: str) -> dict:
    token = upstox_token(account_id)
    if not token:
        raise ValueError(f"{account_id}: no token — set UPSTOX_x_ANALYTICS_TOKEN or login first")
    resp = requests.get(f"{UPSTOX_API}/portfolio/long-term-holdings",
                        headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    rows = [{
        "symbol": h.get("trading_symbol", h.get("tradingsymbol", "")),
        "isin": h.get("isin", ""),
        "quantity": h.get("quantity", 0),
        "avg_price": h.get("average_price", 0),
        "last_price": h.get("last_price", 0),
    } for h in resp.json().get("data", [])]
    count = len(portfolio.set_manual_holdings(account_id, rows)["rows"])
    _mark_source(account_id, "upstox api")
    return {account_id: count}


def upstox_ipos(account_id: str, status: str = "open") -> list:
    """Public IPO list from Upstox's GET /v2/ipos (read-only). Cleaner than NSE:
    each row carries the exchange ``symbol`` and a plain ISO close date, so it
    feeds the tracker (and applied-IPO allotment matching) without scraping.
    status is one of open / upcoming / closed / listed. Uses the same token as
    holdings, so it needs the Static IP just like the other account APIs."""
    token = upstox_token(account_id)
    if not token:
        raise ValueError(f"{account_id}: no token — set UPSTOX_x_ANALYTICS_TOKEN or login first")
    resp = requests.get(f"{UPSTOX_API}/ipos", params={"status": status},
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                        timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


# ---- shared ----------------------------------------------------------------

def _mark_source(account_id: str, source: str) -> None:
    holdings = store.load("holdings", default={})
    if account_id in holdings:
        holdings[account_id]["source"] = source
        store.save("holdings", holdings)


def sync(account_id: str) -> dict:
    kind = _broker_type(account_id)
    if kind == "kite":
        return kite_sync(account_id)
    if kind == "upstox":
        return upstox_sync(account_id)
    raise ValueError(f"{account_id}: no API sync (manual/CSV only)")


def login_url(account_id: str) -> str:
    kind = _broker_type(account_id)
    if kind == "kite":
        return kite_login_url(account_id)
    if kind == "upstox":
        return upstox_login_url(account_id)
    raise ValueError(f"{account_id}: no API login")


def headless_login(account_id: str) -> None:
    """Unattended login for the morning refresh job (refresh_tokens.py).

    Kite goes fully headless via stored credentials + TOTP. Upstox uses a
    long-lived Analytics Token instead (no daily login needed) when
    UPSTOX_x_ANALYTICS_TOKEN is set; without it there's nothing to automate, so
    it raises NotImplementedError and the caller falls back to a phone nudge to
    log in at the dashboard."""
    kind = _broker_type(account_id)
    if kind == "kite":
        kite_headless_login(account_id)
        return
    if kind == "upstox" and _env_opt(account_id, "ANALYTICS_TOKEN"):
        return  # long-lived Analytics Token — already valid, nothing to log in
    raise NotImplementedError(f"{account_id}: no headless login — set UPSTOX_x_ANALYTICS_TOKEN or log in at the dashboard")

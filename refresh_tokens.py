"""Unattended morning token refresh. Windows Task Scheduler runs this ~07:45
IST — after both brokers' nightly expiry (Upstox ~03:30, Kite ~07:30) — so
every API account is logged in and synced before you open the dashboard.

Kite accounts (and the Coin MF folio that rides a Kite session) log in fully
headless from stored credentials + a TOTP seed (investlib/brokers). Upstox has
no reliable headless login, so it — and any Kite account whose auto-login
fails — gets a single ntfy.sh nudge to log in at the dashboard. You find out
before you trust stale numbers.

No portfolio figures ever leave the machine: the push names only which
account needs attention, never a holding or value (same rule as daily_brief.py).
"""

import sys

try:
    import requests  # optional — only needed to push ntfy alerts (requirements-invest.txt)
except ImportError:
    requests = None

import config  # noqa: F401  (loads .env)
from investlib import brokers


def _push(title: str, message: str) -> None:
    topic = config.notification_topic()
    if not topic:
        print(f"INVESTMENTS_NTFY_TOPIC not set; would push — {title}: {message}")
        return
    if requests is None:
        print(f"requests not installed (pip install -r requirements-invest.txt); "
              f"skipping phone push — {title}: {message}")
        return
    requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                  headers={"Title": title, "Tags": "closed_lock_with_key"},
                  timeout=30)


def refresh_accounts() -> tuple[list[str], dict[str, str]]:
    """Log in + sync every configured API account that can go headless.

    Returns (needs_login, sync_blocked):
      needs_login   — accounts where a manual login at the dashboard will help
                      (no headless path, auto-login failed, or the broker rejected the token)
      sync_blocked  — {account: reason} where the token was accepted but the holdings
                      fetch is blocked for another reason (e.g. a static-IP rule, or the
                      broker being unreachable). Telling the user to log in again would
                      send them down the wrong path, so these are reported separately.
    """
    status = brokers.token_status()
    needs_login: list[str] = []
    sync_blocked: dict[str, str] = {}
    for account_id, _kind in brokers.api_accounts():
        if not status[account_id]["configured"]:
            continue  # no credentials for this account — nothing to refresh
        try:
            brokers.headless_login(account_id)
        except NotImplementedError:
            needs_login.append(account_id)  # no headless path for this account
            continue
        except Exception as e:  # a broker flake shouldn't abort the other accounts
            print(f"{account_id}: auto-login failed: {e}", file=sys.stderr)
            needs_login.append(account_id)
            continue
        try:
            print(f"{account_id}: refreshed + synced {brokers.sync(account_id)}")
        except Exception as e:
            info = brokers.classify_sync_error(e)
            print(f"{account_id}: sync failed ({info['kind']}): {info['message']}", file=sys.stderr)
            if info["kind"] == "auth":
                needs_login.append(account_id)
            else:
                sync_blocked[account_id] = info["message"]
    return needs_login, sync_blocked


def refresh_all() -> list[str]:
    """Accounts that still need a manual login at the dashboard (compat wrapper)."""
    return refresh_accounts()[0]


def compose_push(needs_login: list[str], sync_blocked: dict[str, str]):
    """(title, body) for the morning push, or None when everything refreshed.

    Account ids and fixed reason text only — never a balance, holding or token."""
    lines = []
    if needs_login:
        lines.append("Log in at the dashboard for: " + ", ".join(needs_login))
    lines.extend(f"{account}: {reason}" for account, reason in sync_blocked.items())
    if not lines:
        return None
    if needs_login and sync_blocked:
        title = "Broker accounts need attention"
    elif needs_login:
        title = "Broker login needed"
    else:
        title = "Broker sync failing"
    return title, "\n".join(lines)


if __name__ == "__main__":
    needs_login, sync_blocked = refresh_accounts()
    push = compose_push(needs_login, sync_blocked)
    if push:
        _push(*push)
        print(push[0] + ": " + push[1].replace("\n", " | "))
    else:
        print("all API accounts refreshed")

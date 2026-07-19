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

import os
import sys

try:
    import requests  # optional — only needed to push ntfy alerts (requirements-invest.txt)
except ImportError:
    requests = None

import config  # noqa: F401  (loads .env)
from investlib import brokers


def _push(title: str, message: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"NTFY_TOPIC not set; would push — {title}: {message}")
        return
    if requests is None:
        print(f"requests not installed (pip install -r requirements-invest.txt); "
              f"skipping phone push — {title}: {message}")
        return
    requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                  headers={"Title": title, "Tags": "closed_lock_with_key"},
                  timeout=30)


def refresh_all() -> list[str]:
    """Log in + sync every configured API account that can go headless.
    Returns the accounts that still need a manual login at the dashboard
    (Upstox always; any Kite account whose auto-login failed)."""
    status = brokers.token_status()
    needs_manual = []
    for account_id, _kind in brokers.api_accounts():
        if not status[account_id]["configured"]:
            continue  # no credentials for this account — nothing to refresh
        try:
            brokers.headless_login(account_id)
            print(f"{account_id}: refreshed + synced {brokers.sync(account_id)}")
        except NotImplementedError:
            needs_manual.append(account_id)  # e.g. upstox-1
        except Exception as e:  # a broker flake shouldn't abort the other accounts
            print(f"{account_id}: auto-login failed: {e}", file=sys.stderr)
            needs_manual.append(account_id)
    return needs_manual


if __name__ == "__main__":
    manual = refresh_all()
    if manual:
        _push("Broker login needed", "Log in at the dashboard for: " + ", ".join(manual))
        print("needs manual login: " + ", ".join(manual))
    else:
        print("all API accounts refreshed")

"""Live invest-account values -> injected "liveSync" rows in the finances
Portfolio section. Pure functions (no I/O) except inject_live_rows, which
reads the invest store; server.py owns writing the result back to
finances.json (or, for GET, just serving it).

The injected rows are server-owned: a stale browser can never persist a
tampered liveSync row back to disk, because inject_live_rows() is re-run on
every PUT before anything is saved (see server.py).
"""

import re
from datetime import date, datetime

from . import portfolio, store

# A broker-API snapshot older than this many days is "stale": the nightly sync is
# failing or not running. 4 days covers a normal long weekend of closed markets.
API_STALE_DAYS = 4
# Manual / CSV / xlsx snapshots never refresh themselves; nudge after about a month
# (same cadence as analysis.BOND_REFRESH_STALE_DAYS).
IMPORT_STALE_DAYS = 30
_API_SOURCE_RE = re.compile(r"\bapi\b", re.I)   # mirrors BROKER_SOURCE_RE in public/app.js

# investlib account asset_class -> portfolio subCategory shown in the UI.
ASSET_CLASS_SUBCATEGORY = {
    "stocks": "Equity",
    "managed_stocks": "Equity",
    "mutual_funds": "Mutual funds",
    "bonds": "Debt",
}


def account_totals(holdings_by_account: dict) -> dict:
    """Per account: {invested, current}. One shared rule (portfolio.position_totals):
    a holding with no known cost basis is carried at its current value, so it stays in
    the total but cannot inflate the Portfolio page's return."""
    totals = {}
    for account_id, snapshot in (holdings_by_account or {}).items():
        rows = snapshot.get("rows", []) if isinstance(snapshot, dict) else []
        t = portfolio.position_totals(rows)
        totals[account_id] = {"invested": t["invested"], "current": t["current"]}
    return totals


def _day(text):
    """The calendar day of an ISO date/datetime string, or None if unparseable."""
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def is_api_source(source) -> bool:
    """True when a holdings source names a broker API (it refreshes itself)."""
    return bool(_API_SOURCE_RE.search(str(source or "")))


def stale_after_days(source) -> int:
    """Days after which a snapshot from this source counts as stale."""
    return API_STALE_DAYS if is_api_source(source) else IMPORT_STALE_DAYS


def snapshot_age_days(as_of, now_iso):
    """Whole days from a holdings snapshot's as_of date to now; None if unknown."""
    then, now = _day(as_of), _day(now_iso)
    if then is None or now is None:
        return None
    return max((now - then).days, 0)


def live_rows(accounts: list, holdings_by_account: dict, now_iso: str) -> list:
    """One portfolio row per account that has holdings AND a non-empty owner.

    An account with no holdings of its own is skipped — this naturally
    excludes smallcase-1 (whose positions live under the linked kite-1/-2
    accounts and would otherwise be double-counted) as well as any other
    account nobody has synced yet.
    """
    totals = account_totals(holdings_by_account)
    rows = []
    for acct in accounts or []:
        account_id = acct.get("id")
        owner = (acct.get("owner") or "").strip()
        if not owner:
            continue
        snapshot = (holdings_by_account or {}).get(account_id)
        if not snapshot or not snapshot.get("rows"):
            continue
        totals_for_account = totals.get(account_id, {"invested": 0.0, "current": 0.0})
        source = snapshot.get("source", "")
        as_of = snapshot.get("as_of", "")
        age_days = snapshot_age_days(as_of, now_iso)
        limit = stale_after_days(source)
        rows.append({
            "id": f"live-{account_id}",
            "liveSync": True,
            "investAccountId": account_id,
            "category": acct.get("label", account_id),
            "subCategory": ASSET_CLASS_SUBCATEGORY.get(acct.get("asset_class"), "Equity"),
            "owner": owner,
            "invested": totals_for_account["invested"],
            "currentValue": totals_for_account["current"],
            # the snapshot's OWN date. This used to be now_iso, which stamped a
            # 72-day-old snapshot "updated just now" and hid a failing sync.
            "lastSynced": as_of or "",
            "ageDays": age_days,
            "stale": age_days is not None and age_days > limit,
            # holdings snapshot source ("kite api", "upstox api", "kite api (coin)",
            # "manual", or an imported CSV/xlsx filename) — lets the main app tell a
            # true broker-API live sync apart from manual/CSV/xlsx entry made in the
            # Investments tab (see public/app.js liveSyncBadge()).
            "source": source,
        })
    return rows


def inject_live_rows(doc: dict) -> dict:
    """Return doc with doc["portfolio"] = (existing non-liveSync rows) + fresh live rows.

    Also ensures every live-row owner is present in doc["settings"]["persons"].
    Reads the invest store (accounts.json + holdings.json) fresh each call —
    callers (server.py) decide whether that read happens per GET, per PUT, etc.
    """
    accounts = store.accounts()
    holdings = store.load("holdings", default={})
    now_iso = datetime.now().isoformat(timespec="seconds")
    fresh = live_rows(accounts, holdings, now_iso)

    out = dict(doc)
    out["portfolio"] = [r for r in (doc.get("portfolio") or []) if not r.get("liveSync")] + fresh

    settings = dict(out.get("settings") or {})
    persons = list(settings.get("persons") or [])
    changed = False
    for row in fresh:
        if row["owner"] and row["owner"] not in persons:
            persons.append(row["owner"])
            changed = True
    if changed:
        settings["persons"] = persons
        out["settings"] = settings

    return out

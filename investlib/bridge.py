"""Live invest-account values -> injected "liveSync" rows in the finances
Portfolio section. Pure functions (no I/O) except inject_live_rows, which
reads the invest store; server.py owns writing the result back to
finances.json (or, for GET, just serving it).

The injected rows are server-owned: a stale browser can never persist a
tampered liveSync row back to disk, because inject_live_rows() is re-run on
every PUT before anything is saved (see server.py).
"""

from datetime import datetime

from . import store

# investlib account asset_class -> portfolio subCategory shown in the UI.
ASSET_CLASS_SUBCATEGORY = {
    "stocks": "Equity",
    "managed_stocks": "Equity",
    "mutual_funds": "Mutual funds",
    "bonds": "Debt",
}


def account_totals(holdings_by_account: dict) -> dict:
    """Per account: {invested: Σ qty*avg_price, current: Σ (value or qty*last_price)}."""
    totals = {}
    for account_id, snapshot in (holdings_by_account or {}).items():
        rows = snapshot.get("rows", []) if isinstance(snapshot, dict) else []
        invested = 0.0
        current = 0.0
        for row in rows:
            qty = row.get("quantity") or 0
            avg = row.get("avg_price") or 0
            last = row.get("last_price") or 0
            # Wint Wealth bond rows carry total_invested instead of avg_price
            row_invested = row.get("total_invested")
            invested += row_invested if row_invested is not None else qty * avg
            value = row.get("value")
            current += value if value is not None else qty * last
        totals[account_id] = {"invested": round(invested, 2), "current": round(current, 2)}
    return totals


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
        rows.append({
            "id": f"live-{account_id}",
            "liveSync": True,
            "investAccountId": account_id,
            "category": acct.get("label", account_id),
            "subCategory": ASSET_CLASS_SUBCATEGORY.get(acct.get("asset_class"), "Equity"),
            "owner": owner,
            "invested": totals_for_account["invested"],
            "currentValue": totals_for_account["current"],
            "lastSynced": now_iso,
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

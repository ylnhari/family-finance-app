"""The investments dashboard's front page in one call.

Answers three questions in this order, because that is the order people ask them:
  1. How much do we have, and how is it doing?       -> total
  2. Whose is it, and in which accounts?              -> people, accounts
  3. Is there anything I have to do or distrust?      -> attention

Everything is derived from the stores on every call (no cache), so it can never
disagree with the Portfolio page: both go through portfolio.position_totals and the
bridge's staleness rules. Pure read-only aggregation; no network and no writes.
"""

from datetime import date

from . import analysis, bridge, brokers, ipo, portfolio, store

# A bond cashflow this close is worth a heads-up on the front page.
BOND_PAYOUT_WINDOW_DAYS = 30


def _pct(gain: float, base: float):
    """Gain as a percentage of what was invested, or None when nothing was invested."""
    return round(gain / base * 100, 2) if base else None


def _person_key(owner: str) -> str:
    return (owner or "").strip().casefold()


def _account_rows(accounts: list, holdings: dict, sync: dict, today: date) -> list:
    out = []
    for acct in accounts:
        snapshot = holdings.get(acct["id"]) or {}
        rows = snapshot.get("rows") or []
        totals = portfolio.position_totals(rows)
        as_of = snapshot.get("as_of", "")
        source = snapshot.get("source", "")
        age = bridge.snapshot_age_days(as_of, today.isoformat())
        out.append({
            "id": acct["id"],
            "label": acct.get("label") or acct["id"],
            "owner": (acct.get("owner") or "").strip(),
            "asset_class": acct.get("asset_class", "other"),
            "broker_type": acct.get("broker_type", ""),
            "has_data": bool(rows),
            "positions": len(rows),
            "value": totals["current"],
            "invested": totals["invested"],
            "gain": totals["gain"],
            "gain_pct": _pct(totals["gain"], totals["invested"]),
            "unpriced": totals["unpriced"],
            "as_of": as_of,
            "age_days": age,
            "stale": bool(rows) and age is not None and age > bridge.stale_after_days(source),
            "api": bridge.is_api_source(source),
            "source": source,
            "sync": sync.get(acct["id"]),
        })
    return out


def _people(account_rows: list, total_value: float) -> list:
    """One entry per owner (matched ignoring case/spacing), unassigned last."""
    groups = {}
    for a in account_rows:
        if not a["has_data"]:
            continue
        key = _person_key(a["owner"])
        g = groups.setdefault(key, {"owner": a["owner"], "value": 0.0, "invested": 0.0,
                                    "unpriced": 0, "accounts": []})
        g["value"] += a["value"]
        g["invested"] += a["invested"]
        g["unpriced"] += a["unpriced"]
        g["accounts"].append(a["id"])
    people = []
    for g in groups.values():
        gain = g["value"] - g["invested"]
        people.append({
            "owner": g["owner"],
            "value": round(g["value"], 2),
            "invested": round(g["invested"], 2),
            "gain": round(gain, 2),
            "gain_pct": _pct(gain, g["invested"]),
            "share": round(g["value"] / total_value * 100, 1) if total_value else 0.0,
            "unpriced": g["unpriced"],
            "accounts": g["accounts"],
        })
    people.sort(key=lambda p: (p["owner"] == "", -p["value"]))
    return people


def _next_bond_payout(today: date):
    upcoming = sorted((r for r in analysis.bonds()
                       if r.get("next_cashflow_date") and r["next_cashflow_date"] >= today.isoformat()),
                      key=lambda r: r["next_cashflow_date"])
    if not upcoming:
        return None
    first = upcoming[0]["next_cashflow_date"]
    same_day = [r for r in upcoming if r["next_cashflow_date"] == first]
    days = (date.fromisoformat(first) - today).days
    return {"date": first, "days": days, "count": len(same_day),
            "amount": round(sum(r.get("next_cashflow_amount") or 0 for r in same_day), 2),
            "soon": days <= BOND_PAYOUT_WINDOW_DAYS}


def _attention(account_rows: list, today: date) -> dict:
    open_ipos = ipo.tracked(today=today)
    to_decide = [i for i in open_ipos if not i.get("applied")]
    soonest = min(to_decide, key=lambda i: i["days_to_close"], default=None)
    with_data = [a for a in account_rows if a["has_data"]]
    return {
        "ipos": {
            "to_decide": len(to_decide),
            "applied": sum(1 for i in open_ipos if i.get("applied")),
            "closing_soon": sum(1 for i in to_decide if i.get("closing_soon")),
            "next_close": ({"name": soonest["name"], "days": soonest["days_to_close"]}
                           if soonest else None),
        },
        "bonds": {"next_payout": _next_bond_payout(today)},
        "flagged": len(analysis.signals()),
        "stale": [{"account": a["id"], "label": a["label"], "age_days": a["age_days"],
                   "kind": "api" if a["api"] else "import", "source": a["source"]}
                  for a in with_data if a["stale"]],
        "sync_failing": [{"account": a["id"], "label": a["label"], "kind": a["sync"].get("kind"),
                          "message": a["sync"].get("message")}
                         for a in account_rows if a["sync"] and a["sync"].get("ok") is False],
        # holdings with no owner are missing from the Portfolio page (it is per owner)
        "unassigned": [{"account": a["id"], "label": a["label"]}
                       for a in with_data if not a["owner"]],
    }


def build(today: date = None) -> dict:
    """The whole front page: total, people, accounts, attention. Empty store -> zeros."""
    today = today or date.today()
    accounts = store.accounts()
    holdings = portfolio.all_holdings()
    account_rows = _account_rows(accounts, holdings, brokers.sync_status(), today)

    every_row = [r for a in accounts for r in (holdings.get(a["id"]) or {}).get("rows", [])]
    totals = portfolio.position_totals(every_row)
    dated = sorted(a["as_of"] for a in account_rows if a["has_data"] and a["as_of"])
    return {
        "total": {
            "value": totals["current"],
            "invested": totals["invested"],
            "gain": totals["gain"],
            "gain_pct": _pct(totals["gain"], totals["invested"]),
            "unpriced": totals["unpriced"],
            "accounts": sum(1 for a in account_rows if a["has_data"]),
        },
        "as_of": {"latest": dated[-1] if dated else "", "oldest": dated[0] if dated else ""},
        "people": _people(account_rows, totals["current"]),
        "accounts": sorted(account_rows, key=lambda a: -a["value"]),
        "attention": _attention(account_rows, today),
        "bond_payout_window_days": BOND_PAYOUT_WINDOW_DAYS,
    }

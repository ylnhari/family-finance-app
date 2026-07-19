"""Portfolio state summary and heuristic buy/sell/keep signals.

These are decision-support flags, not advice: they surface positions worth a
look (concentration, deep drawdown, big run-up) so the human decides. The
thresholds are deliberately simple and easy to tune below.
"""

from datetime import date

from . import portfolio, store

CONCENTRATION_PCT = 15.0   # single position above this share of total → review
DRAWDOWN_PCT = -25.0       # unrealized loss beyond this → re-check the thesis
RUNUP_PCT = 60.0           # unrealized gain beyond this → consider partial booking
BOND_REFRESH_STALE_DAYS = 30  # no fresh Wint Wealth statement in this long → nudge for a re-import


def summary() -> dict:
    """Totals per account and per asset class, plus grand total."""
    accounts = {a["id"]: a for a in store.accounts()}
    holdings = portfolio.all_holdings()
    per_account, per_class, total = {}, {}, 0.0
    for acct_id, snapshot in holdings.items():
        value = round(sum(r["value"] for r in snapshot["rows"]), 2)
        asset_class = accounts.get(acct_id, {}).get("asset_class", "other")
        per_account[acct_id] = {
            "value": value,
            "as_of": snapshot["as_of"],
            "positions": len(snapshot["rows"]),
            "asset_class": asset_class,
            "source": snapshot.get("source", ""),
        }
        per_class[asset_class] = round(per_class.get(asset_class, 0.0) + value, 2)
        total = round(total + value, 2)
    missing = [a for a in accounts if a not in holdings]
    return {"total": total, "per_account": per_account, "per_asset_class": per_class,
            "accounts_without_data": missing}


def bonds() -> list:
    """Flat list of bond positions (Wint Wealth etc.) with maturity/cashflow
    detail, sorted by maturity date. Rows without a maturity_date (e.g. a
    manually-entered bond with no statement import yet) are skipped."""
    accounts = {a["id"]: a for a in store.accounts()}
    holdings = portfolio.all_holdings()
    rows = []
    for acct_id, snapshot in holdings.items():
        if accounts.get(acct_id, {}).get("asset_class") != "bonds":
            continue
        for row in snapshot["rows"]:
            if not row.get("maturity_date"):
                continue
            rows.append({**row, "account": acct_id})
    rows.sort(key=lambda r: r["maturity_date"])
    return rows


def bonds_needing_refresh(today: date = None) -> list:
    """Bond accounts whose last imported statement is stale — decision
    support for when to re-download and re-import a fresh Wint Wealth
    statement. Accounts never imported at all are left to
    accounts_without_data (summary()); this only flags going-stale data."""
    today = today or date.today()
    accounts = {a["id"]: a for a in store.accounts()}
    holdings = portfolio.all_holdings()
    stale = []
    for acct_id, acct in accounts.items():
        if acct.get("asset_class") != "bonds":
            continue
        snapshot = holdings.get(acct_id)
        if snapshot is None:
            continue
        days_stale = (today - date.fromisoformat(snapshot["as_of"])).days
        if days_stale >= BOND_REFRESH_STALE_DAYS:
            stale.append({"account": acct_id, "label": acct["label"],
                          "as_of": snapshot["as_of"], "days_stale": days_stale})
    return stale


def signals() -> list:
    """Flat list of flagged positions across all accounts."""
    holdings = portfolio.all_holdings()
    total = sum(r["value"] for snap in holdings.values() for r in snap["rows"])
    flags = []
    for acct_id, snapshot in holdings.items():
        for row in snapshot["rows"]:
            share = row["value"] / total * 100 if total else 0.0
            reasons = []
            if share >= CONCENTRATION_PCT:
                reasons.append(f"{share:.1f}% of portfolio — concentration risk")
            if row["pnl_pct"] <= DRAWDOWN_PCT:
                reasons.append(f"{row['pnl_pct']:.1f}% down — re-check the thesis")
            if row["pnl_pct"] >= RUNUP_PCT:
                reasons.append(f"{row['pnl_pct']:.1f}% up — consider partial profit booking")
            if reasons:
                flags.append({
                    "account": acct_id,
                    "symbol": row["symbol"],
                    "value": row["value"],
                    "pnl_pct": row["pnl_pct"],
                    "portfolio_share_pct": round(share, 2),
                    "reasons": reasons,
                })
    flags.sort(key=lambda f: f["value"], reverse=True)
    return flags

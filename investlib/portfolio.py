"""Holdings import from broker CSV exports.

No broker APIs (Kite Connect is paid); instead you download the holdings CSV
from each broker's console and drop it in imports/. The parser is
header-alias based so the same code reads Kite Console, Upstox and Coin
exports without per-broker subclasses. Wint Wealth has no plain CSV export,
but its own xlsx "Holding Statement" / "Upcoming Cashflow Statement"
reports are parsed directly by wintwealth.build_bond_rows via
import_wint_statement below. set_manual_holdings remains as a fallback for
brokers with no exportable report at all.
"""

import csv
from datetime import date
from pathlib import Path

from . import store, wintwealth

# canonical field -> header names seen across Kite Console / Upstox / Coin exports
HEADER_ALIASES = {
    "symbol": ["symbol", "instrument", "tradingsymbol", "scheme name", "company name", "stock name"],
    "isin": ["isin"],
    "quantity": ["quantity available", "quantity", "qty", "qty.", "units", "net quantity"],
    "avg_price": ["average price", "avg. cost", "avg cost", "avg price", "average cost price", "purchase nav", "buy average"],
    "last_price": ["previous closing price", "ltp", "last price", "closing price", "current nav", "close price"],
}

_NUMERIC = {"quantity", "avg_price", "last_price"}


def _match_headers(fieldnames):
    """Map canonical field -> actual CSV column name, or None if absent."""
    normalized = {name.strip().lower(): name for name in fieldnames if name}
    mapping = {}
    for field, aliases in HEADER_ALIASES.items():
        mapping[field] = next((normalized[a] for a in aliases if a in normalized), None)
    return mapping


def _to_float(raw) -> float:
    if raw is None:
        return 0.0
    cleaned = str(raw).replace(",", "").replace("₹", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_holdings_csv(path: Path) -> list:
    """Return [{symbol, isin, quantity, avg_price, last_price, value, pnl_pct}]."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"{path.name}: empty or not a CSV")
        mapping = _match_headers(reader.fieldnames)
        if not mapping["symbol"] or not mapping["quantity"]:
            raise ValueError(
                f"{path.name}: could not find symbol/quantity columns "
                f"(headers: {reader.fieldnames})"
            )
        rows = []
        for raw in reader:
            row = {}
            for field, column in mapping.items():
                value = raw.get(column) if column else None
                row[field] = _to_float(value) if field in _NUMERIC else (value or "").strip()
            if not row["symbol"] or row["quantity"] <= 0:
                continue
            row["value"] = round(row["quantity"] * row["last_price"], 2)
            invested = row["quantity"] * row["avg_price"]
            row["pnl_pct"] = round((row["value"] - invested) / invested * 100, 2) if invested else 0.0
            rows.append(row)
    return rows


def import_holdings(account_id: str, csv_path: Path) -> dict:
    """Parse a broker CSV and store it as today's snapshot for the account."""
    if account_id not in {a["id"] for a in store.accounts()}:
        raise ValueError(f"unknown account: {account_id}")
    rows = parse_holdings_csv(csv_path)
    holdings = store.load("holdings", default={})
    holdings[account_id] = {
        "as_of": date.today().isoformat(),
        "source": csv_path.name,
        "rows": rows,
    }
    store.save("holdings", holdings)
    return holdings[account_id]


def set_manual_holdings(account_id: str, rows: list) -> dict:
    """For brokers without CSV export (Wint Wealth bonds): store rows directly.

    Each row: {symbol, quantity, avg_price, last_price} — value/pnl computed.
    """
    if account_id not in {a["id"] for a in store.accounts()}:
        raise ValueError(f"unknown account: {account_id}")
    cleaned = []
    for r in rows:
        qty, avg, ltp = _to_float(r.get("quantity")), _to_float(r.get("avg_price")), _to_float(r.get("last_price"))
        value = round(qty * ltp, 2)
        invested = qty * avg
        cleaned.append({
            "symbol": str(r.get("symbol", "")).strip(),
            "isin": str(r.get("isin", "")).strip(),
            "quantity": qty,
            "avg_price": avg,
            "last_price": ltp,
            "value": value,
            "pnl_pct": round((value - invested) / invested * 100, 2) if invested else 0.0,
        })
    holdings = store.load("holdings", default={})
    holdings[account_id] = {"as_of": date.today().isoformat(), "source": "manual", "rows": cleaned}
    store.save("holdings", holdings)
    return holdings[account_id]


def import_wint_statement(account_id: str, holding_path: Path, cashflow_path: Path = None,
                          summary_path: Path = None) -> dict:
    """Parse Wint Wealth's own xlsx exports (Holding Statement, optionally
    paired with Upcoming Cashflow Statement, and an Investment Summary for
    purchase dates) and store as today's snapshot. Passing the multi-sheet
    master workbook as ``holding_path`` supplies all three at once.
    """
    if account_id not in {a["id"] for a in store.accounts()}:
        raise ValueError(f"unknown account: {account_id}")
    rows = wintwealth.build_bond_rows(holding_path, cashflow_path, summary_path)
    holdings = store.load("holdings", default={})
    holdings[account_id] = {"as_of": date.today().isoformat(), "source": holding_path.name, "rows": rows}
    store.save("holdings", holdings)
    return holdings[account_id]


def all_holdings() -> dict:
    return store.load("holdings", default={})

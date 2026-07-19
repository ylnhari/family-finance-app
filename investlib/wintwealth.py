"""Parser for Wint Wealth's own xlsx report exports (Holding Statement +
Upcoming Cashflow Statement). Unlike Kite/Upstox/Coin, Wint Wealth has no
plain CSV export, but the "current holdings" and "upcoming cashflows"
reports it does offer are structured enough to parse directly — no need
for manual entry.
"""

from datetime import date, datetime
from pathlib import Path

from . import xlsx_lite

HOLDING_SHEET = "Holding Statement"
CASHFLOW_SHEET = "Upcoming Cashflow Statement"
INVEST_SHEET = "Investment Summary Report"  # only in the multi-sheet "master" export

_HOLDING_HEADER = "Name Of Bond"
_INVEST_HEADER = "Bond Name"  # the Investment Summary table repeats this header per FY block
_CASHFLOW_COLS = 10  # per-transaction table only; FY/monthly summaries sit to the right, ignored


def _to_float(raw) -> float:
    if raw in (None, ""):
        return 0.0
    try:
        return float(str(raw).replace(",", "").strip())
    except ValueError:
        return 0.0


def _pct_to_float(raw):
    """'11.50%' -> 11.5 ; '-'/'' -> None (XIRR is blank for some lots)."""
    s = str(raw or "").replace("%", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_ddmmyyyy(raw: str) -> str:
    """Wint dates come as dd-mm-yyyy (Holding/Cashflow) or dd/mm/yyyy
    (Investment Summary) — accept either separator."""
    raw = raw.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {raw!r}")


def _data_rows_after_header(rows: list, header_first_cell: str) -> list:
    """Rows strictly between the header row (first cell match) and the
    first blank/Total row that follows it."""
    start = next((i for i, r in enumerate(rows) if r and r[0].strip() == header_first_cell), None)
    if start is None:
        raise ValueError(f"could not find header row starting with {header_first_cell!r}")
    data = []
    for row in rows[start + 1:]:
        name = row[0].strip() if row else ""
        if not name or name == "Total":
            break
        data.append(row)
    return data


def parse_holding_statement(path: Path) -> list:
    """Return [{symbol, isin, maturity_date, quantity, ytm_pct, current_value,
    total_invested, upcoming_interest_before_tds, upcoming_principal,
    principal_repaid_till_date, interest_received_before_tds_till_date,
    interest_received_after_tds_till_date, tds_deducted_till_date,
    principal_repayment_type, interest_repayment_type}]."""
    rows = xlsx_lite.read_sheet_by_name(path, HOLDING_SHEET)
    bonds = []
    for row in _data_rows_after_header(rows, _HOLDING_HEADER):
        row = row + [""] * (17 - len(row))
        bonds.append({
            "symbol": row[0].strip(),
            "isin": row[1].strip(),
            "maturity_date": _parse_ddmmyyyy(row[2]),
            "quantity": _to_float(row[3]),
            "ytm_pct": _to_float(row[4]),
            "current_value": _to_float(row[5]),
            "upcoming_interest_before_tds": _to_float(row[7]),
            "upcoming_principal": _to_float(row[8]),
            "total_invested": _to_float(row[9]),
            "principal_repaid_till_date": _to_float(row[11]),
            "interest_received_before_tds_till_date": _to_float(row[12]),
            "interest_received_after_tds_till_date": _to_float(row[13]),
            "tds_deducted_till_date": _to_float(row[14]),
            "principal_repayment_type": row[15].strip(),
            "interest_repayment_type": row[16].strip(),
        })
    return bonds


def parse_upcoming_cashflows(path: Path) -> list:
    """Return every future transaction row:
    [{isin, date, amount_in_bank, principal_repaid, interest_after_tds,
    interest_before_tds, tds, sell_value, txn_type}]."""
    rows = xlsx_lite.read_sheet_by_name(path, CASHFLOW_SHEET)
    out = []
    for row in _data_rows_after_header(rows, _HOLDING_HEADER):
        row = (row + [""] * _CASHFLOW_COLS)[:_CASHFLOW_COLS]
        out.append({
            "symbol": row[0].strip(),
            "isin": row[1].strip(),
            "date": _parse_ddmmyyyy(row[2]),
            "amount_in_bank": _to_float(row[3]),
            "principal_repaid": _to_float(row[4]),
            "interest_after_tds": _to_float(row[5]),
            "interest_before_tds": _to_float(row[6]),
            "tds": _to_float(row[7]),
            "sell_value": _to_float(row[8]),
            "txn_type": row[9].strip(),
        })
    return out


def parse_investment_summary(path: Path) -> list:
    """Parse the 'Investment Summary Report' sheet (only present in the master
    workbook) — the one place Wint records each purchase. Returns one entry per
    buy lot: [{bond_name, isin, units, invested_amount, face_value,
    acquisition_cost, purchase_date, maturity_date, xirr_pct,
    interest_frequency, principal_frequency}].

    The sheet repeats a `Bond Name … Date of Investment … XIRR` header once per
    financial-year block, each block ending in a 'Total:' row, so we collect the
    data rows that follow every header row and stop each block at Total/blank.
    """
    rows = xlsx_lite.read_sheet_by_name(path, INVEST_SHEET)
    out = []
    for i, row in enumerate(rows):
        if not row or (row[0].strip() if row else "") != _INVEST_HEADER:
            continue
        for r in rows[i + 1:]:
            first = r[0].strip() if r else ""
            second = r[1].strip() if len(r) > 1 else ""
            if not first and not second:
                break                       # blank separator ends the block
            if second.startswith("Total") or first.startswith("Date of report"):
                break
            if first == _INVEST_HEADER:
                break                       # next block's header (shouldn't hit, but safe)
            r = r + [""] * (11 - len(r))
            out.append({
                "bond_name": r[0].strip(),
                "isin": r[1].strip(),
                "units": _to_float(r[2]),
                "invested_amount": _to_float(r[3]),
                "face_value": _to_float(r[4]),
                "acquisition_cost": _to_float(r[5]),
                "purchase_date": _parse_ddmmyyyy(r[6]),
                "maturity_date": _parse_ddmmyyyy(r[7]),
                "xirr_pct": _pct_to_float(r[8]),
                "interest_frequency": r[9].strip(),
                "principal_frequency": r[10].strip(),
            })
    return out


def _enrich_with_purchases(bonds: list, summary: list) -> None:
    """Attach purchase_date / xirr_pct / frequencies from the Investment Summary
    onto each holding lot, matching by ISIN + invested amount so bonds bought as
    a SIP (same ISIN, several buy dates) keep each lot's own date. Falls back to
    the earliest purchase for that ISIN when no lot-level amount matches."""
    from collections import defaultdict
    pool = defaultdict(list)
    for s in summary:
        pool[s["isin"]].append(s)
    earliest = {isin: min(s["purchase_date"] for s in lst) for isin, lst in pool.items()}
    for bond in bonds:
        isin = bond["isin"]
        match = next((s for s in pool.get(isin, [])
                      if abs(s["invested_amount"] - bond["total_invested"]) < 1.0), None)
        if match:
            pool[isin].remove(match)          # each buy lot is consumed once
            bond["purchase_date"] = match["purchase_date"]
            bond["xirr_pct"] = match["xirr_pct"]
            bond["interest_frequency"] = match["interest_frequency"] or bond.get("interest_repayment_type", "")
            bond["principal_frequency"] = match["principal_frequency"] or bond.get("principal_repayment_type", "")
        elif isin in earliest:
            bond["purchase_date"] = earliest[isin]


def build_bond_rows(holding_path: Path, cashflow_path: Path = None,
                    summary_path: Path = None) -> list:
    """Combine the Holding Statement with the Upcoming Cashflow Statement
    (if given) into rows ready for storage: adds after-TDS upcoming
    interest and the next scheduled payment per bond, and the generic
    value/pnl_pct keys the rest of the app already knows how to sum.

    The same ISIN can appear more than once in the Holding Statement (e.g. a
    monthly bond SIP buying the same issue in different months) — each row
    is a separate lot, not a duplicate. The Upcoming Cashflow Statement only
    keys by ISIN though, so when several lots share one, its cashflow rows
    are split across those lots in proportion to each lot's own
    upcoming_interest_before_tds (the one figure the Holding Statement
    already gives per lot).

    When ``holding_path`` is the multi-sheet *master* workbook it carries the
    Upcoming Cashflow and Investment Summary sheets too, so ``cashflow_path`` /
    ``summary_path`` are derived from it automatically — passing the master file
    alone yields cashflows and per-lot purchase dates without extra selections.
    """
    sheets = set(xlsx_lite.sheet_names(holding_path))
    if cashflow_path is None and CASHFLOW_SHEET in sheets:
        cashflow_path = holding_path
    if summary_path is None and INVEST_SHEET in sheets:
        summary_path = holding_path

    bonds = parse_holding_statement(holding_path)
    lots_by_isin = {}
    for bond in bonds:
        lots_by_isin.setdefault(bond["isin"], []).append(bond)

    if cashflow_path is not None:
        cashflows = parse_upcoming_cashflows(cashflow_path)
        txns_by_isin = {}
        for c in cashflows:
            txns_by_isin.setdefault(c["isin"], []).append(c)

        for isin, lots in lots_by_isin.items():
            txns = sorted(txns_by_isin.get(isin, []), key=lambda c: c["date"])
            if not txns:
                continue
            total_after_tds = round(sum(c["interest_after_tds"] for c in txns), 2)
            lots_before_tds_total = sum(lot["upcoming_interest_before_tds"] for lot in lots)
            earliest_date = txns[0]["date"]
            same_day_total = round(sum(c["amount_in_bank"] for c in txns if c["date"] == earliest_date), 2)
            for lot in lots:
                share = (lot["upcoming_interest_before_tds"] / lots_before_tds_total
                         if lots_before_tds_total else 1 / len(lots))
                lot["upcoming_interest_after_tds"] = round(total_after_tds * share, 2)
                lot["next_cashflow_date"] = earliest_date
                lot["next_cashflow_amount"] = round(same_day_total * share, 2)

    if summary_path is not None:
        _enrich_with_purchases(bonds, parse_investment_summary(summary_path))

    rows = []
    for bond in bonds:
        value = bond["current_value"]
        invested = bond["total_invested"]
        upcoming_interest = bond.get("upcoming_interest_after_tds", bond["upcoming_interest_before_tds"])
        row = dict(bond)
        row["value"] = value
        row["pnl_pct"] = round((value - invested) / invested * 100, 2) if invested else 0.0
        row["expected_total_remaining"] = round(upcoming_interest + bond["upcoming_principal"], 2)
        rows.append(row)
    return rows

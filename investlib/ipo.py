"""IPO tracker: record open IPOs with subscription figures, get an apply/skip
call based on how many times each is subscribed.

Subscription numbers change through the bidding window; update an IPO by
adding it again with the same name (upsert). There is no stable free IPO API,
so numbers are entered via CLI/dashboard — typically pasted from Chittorgarh
or the broker app on the last day, when the retail decision is actually made.

Applied-for IPOs (set_applied) additionally carry the fields needed to close
the loop after the bidding window: the exchange ``symbol`` and the allotment/
listing dates. Once shares are allotted they land in the demat holdings the
broker sync already pulls, so allotment_status matches an applied IPO to its
holding *by symbol* and reads back what you got it for (avg price) vs the
current price — the listing-day gain. We never place the IPO bid itself
(that's an order / a payment); this is tracking and reminders only.
"""

from datetime import date, datetime

from . import store

# Apply/skip thresholds on total subscription (times covered).
APPLY_X = 10.0   # heavily oversubscribed → good listing-gain odds, apply
NEUTRAL_X = 1.0  # covered but not hot → apply only on fundamentals
CLOSING_SOON_DAYS = 2

# Fields that describe an application (not the live subscription figures), so
# upsert can preserve them when a later NSE sync refreshes the same IPO's numbers.
APPLIED_FIELDS = ("symbol", "applied", "applied_lots", "applied_amount",
                  "allotment_date", "listing_date")

# Trailing tokens different sources tack on for the same IPO — NSE says
# "Foo Limited", Upstox says "Foo IPO" or just "Foo". Stripped so both fold to
# one tracker row instead of duplicating (same idea as ipo_history._key).
_NAME_SUFFIXES = (" ipo", " private limited", " pvt ltd", " limited", " ltd",
                  " pvt", " private")


def _norm(name: str) -> str:
    """Normalized match key: lowercased, punctuation flattened, trailing
    Limited/Ltd/IPO tokens dropped, so 'Foo & Co Limited' and 'Foo Co IPO'
    match. Only for matching — the stored display name is left as given."""
    n = str(name or "").strip().lower()
    for junk in (".", ",", "-", "(", ")", "&"):
        n = n.replace(junk, " ")
    n = " ".join(n.split())
    changed = True
    while changed:  # peel repeated tails, e.g. "... Limited IPO"
        changed = False
        for suffix in _NAME_SUFFIXES:
            if n.endswith(suffix):
                n, changed = n[: -len(suffix)].strip(), True
    return n


def upsert(name: str, close_date: str, sub_total: float = 0.0,
           sub_qib: float = 0.0, sub_nii: float = 0.0, sub_retail: float = 0.0,
           gmp: float | None = None, notes: str = "", symbol: str | None = None) -> dict:
    """Add or update an IPO (matched case-insensitively by name).

    Subscription figures and notes are refreshed on every call (that's what a
    live NSE sync updates), but any application details already recorded via
    set_applied are carried forward so a re-sync never wipes them. An NSE sync
    can also pass the exchange ``symbol`` here so allotment matching works
    later without manual entry.
    """
    datetime.strptime(close_date, "%Y-%m-%d")  # validate format
    ipos = store.load("ipos", default=[])
    key = _norm(name)
    existing = next((i for i in ipos if _norm(i["name"]) == key), {})
    record = {k: existing[k] for k in APPLIED_FIELDS if k in existing}  # preserve application
    record.update({
        "name": name, "close_date": close_date,
        "sub_total": float(sub_total), "sub_qib": float(sub_qib),
        "sub_nii": float(sub_nii), "sub_retail": float(sub_retail),
        "gmp": gmp, "notes": notes,
        "updated": date.today().isoformat(),
    })
    if symbol:  # empty NSE symbol shouldn't clobber one we already have
        record["symbol"] = symbol.strip().upper()
    ipos = [i for i in ipos if _norm(i["name"]) != key]
    ipos.append(record)
    ipos.sort(key=lambda i: i["close_date"])
    store.save("ipos", ipos)
    return record


def set_applied(name: str, applied: bool = True, symbol: str | None = None,
                applied_lots: float | None = None, applied_amount: float | None = None,
                allotment_date: str | None = None, listing_date: str | None = None) -> dict:
    """Mark (or unmark) that you applied for a tracked IPO, recording the
    exchange symbol and allotment/listing dates so allotment_status can close
    the loop later. Only the fields you pass are changed. Dates are ISO."""
    for d in (allotment_date, listing_date):
        if d:
            datetime.strptime(d, "%Y-%m-%d")  # validate format
    ipos = store.load("ipos", default=[])
    key = _norm(name)
    match = next((i for i in ipos if _norm(i["name"]) == key), None)
    if match is None:
        raise ValueError(f"unknown IPO: {name} (add it to the tracker first)")
    match["applied"] = bool(applied)
    if symbol:
        match["symbol"] = symbol.strip().upper()
    if applied_lots is not None:
        match["applied_lots"] = float(applied_lots)
    if applied_amount is not None:
        match["applied_amount"] = float(applied_amount)
    if allotment_date:
        match["allotment_date"] = allotment_date
    if listing_date:
        match["listing_date"] = listing_date
    store.save("ipos", ipos)
    return match


def recommend(ipo: dict) -> dict:
    sub = ipo.get("sub_total", 0.0)
    if sub >= APPLY_X:
        call, reason = "APPLY", f"subscribed {sub:.1f}x — strong demand, good listing-gain odds"
    elif sub >= NEUTRAL_X:
        call, reason = "NEUTRAL", f"subscribed {sub:.1f}x — covered but not hot; apply only if you like the business"
    else:
        call, reason = "SKIP", f"subscribed {sub:.1f}x — undersubscribed; skip unless long-term conviction"
    qib = ipo.get("sub_qib", 0.0)
    if 0 < qib < 1 and call != "SKIP":
        call, reason = "CAUTION", reason + f"; but QIB only {qib:.1f}x — institutions are cold"
    return {"call": call, "reason": reason}


def tracked(include_closed: bool = False, today: date | None = None) -> list:
    """All tracked IPOs with recommendation and days-to-close attached."""
    today = today or date.today()
    out = []
    for ipo in store.load("ipos", default=[]):
        close = datetime.strptime(ipo["close_date"], "%Y-%m-%d").date()
        days_left = (close - today).days
        if days_left < 0 and not include_closed:
            continue
        item = dict(ipo)
        item["days_to_close"] = days_left
        item["closing_soon"] = 0 <= days_left <= CLOSING_SOON_DAYS
        item.update(recommend(ipo))
        out.append(item)
    return out


def reminders(today: date | None = None) -> list:
    """IPOs closing within CLOSING_SOON_DAYS — the ones needing a decision now."""
    return [i for i in tracked(today=today) if i["closing_soon"]]


# ---- allotment tracking (applied IPOs → holdings) --------------------------

def _holding_for_symbol(symbol: str) -> dict | None:
    """Find the demat holding matching an IPO's exchange symbol across every
    account. Allotted IPO shares show up in the normal broker holdings sync, so
    a match is our proof of allotment (and carries the price we got it at)."""
    if not symbol:
        return None
    want = symbol.strip().upper()
    for account_id, snap in store.load("holdings", default={}).items():
        for row in snap.get("rows", []):
            if str(row.get("symbol", "")).strip().upper() == want:
                return {"account": account_id, **row}
    return None


def allotment_status(today: date = None) -> list:
    """For every IPO marked applied, whether it's allotted yet (its symbol is
    in holdings) and, if so, what we got it for vs the current price — the
    listing-day gain. Statuses: ALLOTTED / NOT_ALLOTTED / AWAITING.

    NOT_ALLOTTED depends on a *fresh* holdings sync (a stale sync could hide a
    real allotment), so it's only asserted once the allotment date has passed;
    before that an un-matched applied IPO reads as AWAITING."""
    today = today or date.today()
    out = []
    for ipo in store.load("ipos", default=[]):
        if not ipo.get("applied"):
            continue
        holding = _holding_for_symbol(ipo.get("symbol"))
        item = {
            "name": ipo["name"], "symbol": ipo.get("symbol", ""),
            "allotment_date": ipo.get("allotment_date"),
            "listing_date": ipo.get("listing_date"),
            "applied_lots": ipo.get("applied_lots"),
            "applied_amount": ipo.get("applied_amount"),
        }
        alloc = ipo.get("allotment_date")
        try:
            alloc_passed = bool(alloc) and datetime.strptime(alloc, "%Y-%m-%d").date() <= today
        except ValueError:
            alloc_passed = False
        if holding:
            got = holding.get("avg_price", 0.0)      # allotment/issue price we paid
            qty = holding.get("quantity", 0.0)
            ltp = holding.get("last_price", 0.0)
            invested = round(got * qty, 2)
            value = holding.get("value", round(ltp * qty, 2))
            item.update({
                "status": "ALLOTTED", "account": holding["account"],
                "quantity": qty, "got_price": got, "last_price": ltp,
                "invested": invested, "current_value": value,
                "gain_amount": round(value - invested, 2),
                "gain_pct": holding.get("pnl_pct"),
            })
        else:
            item["status"] = "NOT_ALLOTTED" if alloc_passed else "AWAITING"
        out.append(item)
    out.sort(key=lambda r: (r.get("listing_date") or r.get("allotment_date") or ""), reverse=True)
    return out


def allotment_reminders(today: date = None) -> list:
    """Applied IPOs whose allotment falls today (a nudge to check the result).

    Deliberately holds no holdings figures — like the other daily-brief pushes,
    an allotment alert leaving the machine says only "check your allotment";
    the allotted/not answer and any P&L stay on the local dashboard."""
    today = today or date.today()
    due = []
    for ipo in store.load("ipos", default=[]):
        if not ipo.get("applied") or not ipo.get("allotment_date"):
            continue
        try:
            when = datetime.strptime(ipo["allotment_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if when == today:
            due.append({"name": ipo["name"], "allotment_date": ipo["allotment_date"]})
    return due

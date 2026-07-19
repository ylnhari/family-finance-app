"""Past-IPO listing history: how earlier IPOs were subscribed, priced, and how
they listed (issue price vs listing price → listing gain).

No single free source has all of it. This module merges three, keyed by symbol
(or name), last non-blank write wins:
  * NSE  /api/public-past-issues — the authoritative *list* of past IPOs with
    open/close/listing dates and SME-vs-mainboard, but issue price is patchy
    and it carries no listing price or subscription (fetch_from_nse).
  * a pasted CSV — the rich fields (issue price, listing price, subscription),
    copied from an aggregator like Chittorgarh; tolerant header matching so any
    reasonable column layout works (import_csv / parse_history_csv).
  * our own live IPO tracker — subscription figures we captured while each IPO
    was open now flow into history once it has closed (carry_from_tracker), so
    coverage grows on its own going forward.

Best-effort throughout (NSE renames fields and rate-limits): unparseable rows
are skipped, never fatal. Listing gain is computed from the two prices when it
isn't given outright.
"""

from datetime import date, datetime, timedelta

from . import store

STORE_KEY = "ipo_history"
DEFAULT_YEARS = 5
NSE_WINDOW_DAYS = 180  # NSE caps the past-issues date range; page it in chunks

# Chittorgarh's "IPO Subscription & Listing Gain" report (id 98) as JSON. This
# is the authoritative, *unadjusted* record of each past IPO's listing-day price
# and its QIB/NII/retail subscription — the fields NSE's feed lacks. The public
# page is bot-walled, but this backing API host answers a plain browser-ish GET.
# It pages ~7 rows at a time (page size is fixed server-side), so we walk pages
# until one comes back empty.
CHITTORGARH_API = "https://webnodejs.chittorgarh.com/cloud/report/data-read/98/{page}/7/{year}/{fy}/0/all/0"
CHITTORGARH_MAX_PAGES = 200  # safety cap (~7/page → covers any year's IPO count)

# canonical field -> the header names an aggregator's CSV might use for it
HEADER_ALIASES = {
    "name": ["ipo name", "company", "company name", "issuer company", "issuer", "name", "security"],
    "symbol": ["symbol", "ticker", "scrip"],
    "segment": ["type", "series", "segment", "category", "board", "exchange"],
    "open_date": ["open date", "issue open", "opening date", "ipo open date", "open"],
    "close_date": ["close date", "issue close", "closing date", "ipo close date", "close"],
    "listing_date": ["listing date", "date of listing", "list date", "listing"],
    "issue_price": ["issue price", "issue price (rs)", "final issue price", "ipo price", "offer price", "cap price"],
    "listing_price": ["listing price", "listing open", "listing open price", "listing day open",
                      "last trade price", "ltp", "list price"],
    "listing_gain_pct": ["listing gains", "listing gain", "listing gain %", "listing gains %",
                         "gain %", "listing %", "listing gain(%)"],
    "sub_total": ["subscription", "total subscription", "total subscription (x)", "sub total",
                  "times subscribed", "overall subscription", "total (x)", "subscription (x)"],
    "sub_qib": ["qib", "qib (x)", "qib subscription", "qib x"],
    "sub_nii": ["nii", "hni", "nii (x)", "hni (x)", "nii subscription"],
    "sub_retail": ["retail", "rii", "retail (x)", "rii (x)", "retail subscription"],
}

_PRICE_FIELDS = ("issue_price", "listing_price", "listing_gain_pct",
                 "sub_total", "sub_qib", "sub_nii", "sub_retail")
_DATE_FIELDS = ("open_date", "close_date", "listing_date")


# ---- parsing helpers -------------------------------------------------------

def _num(raw):
    """Lenient number parse — strips ₹/Rs, commas, %, the ×/x subscription
    suffix and parens. Returns None (not 0) when there's no number, so a blank
    from one source never clobbers a real value from another.

    Case-insensitive: "Rs.94" lower-cases to "rs.94", and stripping the "rs."
    token (dot included) leaves "94" — so an abbreviation dot is never mistaken
    for a decimal point (the "Rs.94 → 0.94" bug)."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    for junk in ("₹", "rs.", "rs", "%", "×", "x", ",", "(", ")"):
        text = text.replace(junk, "")
    text = text.strip()
    if text in ("", "-", "--", "n/a", "na", "nan", "none"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date(raw):
    """Normalize NSE/aggregator dates to ISO, or None. Handles 22-MAR-2024,
    22-Mar-2024, 22-03-2024, 2024-03-22, 22/03/2024."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text in ("", "-", "--"):
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%B-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _segment(raw):
    return "SME" if "sme" in str(raw or "").lower() else "Mainboard"


# trailing corporate-form tokens that carry no identity — stripped so the same
# company folds regardless of which source's spelling we got. Different feeds
# render it differently ("Company Limited" from NSE, "Co.Ltd." from Chittorgarh),
# so we peel these off the end one at a time until none remain.
_CORP_TOKENS = {"limited", "ltd", "pvt", "private", "company", "co",
                "corporation", "corp", "incorporated", "inc"}


def _key(name) -> str:
    """Merge key: a normalized company name. NSE and Chittorgarh both carry the
    *name* (Chittorgarh has no exchange symbol), so we key on it — lowercased,
    punctuation flattened, and every trailing corporate-form token peeled off —
    so "IC Electricals Company Limited" and "IC Electricals Co.Ltd." fold to one."""
    n = str(name or "").strip().lower()
    for junk in (".", ",", "-", "(", ")", "&"):
        n = n.replace(junk, " ")
    tokens = n.split()
    while tokens and tokens[-1] in _CORP_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _gain(record):
    """Listing gain %: explicit value if given, else computed from prices."""
    if record.get("listing_gain_pct") is not None:
        return round(record["listing_gain_pct"], 2)
    issue, listing = record.get("issue_price"), record.get("listing_price")
    if issue and listing:
        return round((listing - issue) / issue * 100, 2)
    return None


# ---- store merge -----------------------------------------------------------

def _merge(existing: dict, incoming: dict, source: str) -> dict:
    """Fold incoming into existing; any non-blank incoming field wins."""
    merged = dict(existing)
    for field, value in incoming.items():
        if field in ("name", "symbol", "segment"):
            if value:  # never blank out an identity we already have
                merged[field] = value
        elif value is not None:
            merged[field] = value
    sources = set(existing.get("sources", [])) | {source}
    merged["sources"] = sorted(sources)
    return merged


def _upsert_all(records: list, source: str) -> dict:
    data = store.load(STORE_KEY, default={})
    added = updated = 0
    for rec in records:
        key = _key(rec.get("name"))
        if not key:
            continue  # no usable identity
        if key in data:
            updated += 1
        else:
            added += 1
        data[key] = _merge(data.get(key, {}), rec, source)
    store.save(STORE_KEY, data)
    return {"added": added, "updated": updated, "count": len(records)}


# ---- NSE past issues -------------------------------------------------------

def parse_nse_issues(items: list) -> list:
    """Turn raw /api/public-past-issues rows into history records (pure)."""
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("companyName") or item.get("company") or item.get("symbol")
        if not name:
            continue
        # issue price: explicit field, else the top of a price band like
        # "Rs.94 to Rs.99" — let _num strip the "Rs." on each token so the
        # abbreviation dot isn't read as a decimal (don't pre-strip here).
        issue = _num(item.get("issuePrice"))
        if issue is None:
            band = str(item.get("priceRange") or "")
            parts = [_num(p) for p in band.replace("to", "-").split("-")]
            parts = [p for p in parts if p is not None]
            issue = max(parts) if parts else None
        out.append({
            "name": str(name).strip(),
            "symbol": (item.get("symbol") or "").strip(),
            "segment": _segment(item.get("securityType") or item.get("series")),
            "open_date": _date(item.get("ipoStartDate") or item.get("issueStartDate")),
            "close_date": _date(item.get("ipoEndDate") or item.get("issueEndDate")),
            "listing_date": _date(item.get("listingDate")),
            "issue_price": issue,
        })
    return out


def fetch_from_nse(years: int = DEFAULT_YEARS, today: date = None) -> dict:
    """Page NSE's past-issues endpoint back `years` years and upsert the list.
    Network/parse failures on a window are skipped, never fatal."""
    from . import ipo_fetch  # reuse the cookie warm-up + browser-ish headers

    today = today or date.today()
    start = today - timedelta(days=int(years * 365.25))
    session = ipo_fetch._session()
    collected = []
    window_start = start
    while window_start <= today:
        window_end = min(window_start + timedelta(days=NSE_WINDOW_DAYS), today)
        params = {"from_date": window_start.strftime("%d-%m-%Y"),
                  "to_date": window_end.strftime("%d-%m-%Y")}
        try:
            resp = session.get(f"{ipo_fetch.NSE}/api/public-past-issues", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            collected.extend(data if isinstance(data, list) else data.get("data", []))
        except Exception:  # NSE flake on one window shouldn't sink the rest
            pass
        window_start = window_end + timedelta(days=1)
    return _upsert_all(parse_nse_issues(collected), source="nse")


# ---- Chittorgarh subscription + listing-gain report ------------------------

def parse_chittorgarh_rows(items: list) -> list:
    """Turn report-98 JSON rows into history records (pure). Listing price is the
    listing-day CLOSE, so the computed listing gain (issue → close) matches the
    figure Chittorgarh itself reports; subscription comes straight from the
    QIB/NII/retail/total columns."""
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Company") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "segment": _segment(item.get("Issue Category")),
            "listing_date": _date(item.get("Listing Date")),
            "issue_price": _num(item.get("Issue Price (Rs.)")),
            "listing_price": _num(item.get("Close Price on Listing (Rs.)")),
            "sub_total": _num(item.get("Total (x)")),
            "sub_qib": _num(item.get("QIB (x)")),
            "sub_nii": _num(item.get("NII (x)")),
            "sub_retail": _num(item.get("Retail (x)")),
        })
    return out


def _chittorgarh_session():
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
        "Accept": "application/json",
        "Referer": "https://www.chittorgarh.com/",
    })
    return s


def fetch_from_chittorgarh(years: int = DEFAULT_YEARS, today: date = None) -> dict:
    """Pull each year's subscription + listing-price rows and upsert them. Rows
    merge by normalized name onto the NSE-sourced list, so listing price, listing
    gain and subscription fill in for IPOs we already track. Per-year/per-page
    network failures are skipped, never fatal."""
    today = today or date.today()
    # Chittorgarh keys the endpoint by the current financial year (Apr–Mar).
    fy = f"{today.year}-{str(today.year + 1)[-2:]}" if today.month >= 4 \
        else f"{today.year - 1}-{str(today.year)[-2:]}"
    session = _chittorgarh_session()
    collected = []
    for year in range(today.year - years, today.year + 1):
        seen = set()
        for page in range(1, CHITTORGARH_MAX_PAGES + 1):
            url = CHITTORGARH_API.format(page=page, year=year, fy=fy)
            try:
                resp = session.get(url, params={"search": "", "v": "19-03"}, timeout=30)
                resp.raise_for_status()
                rows = resp.json().get("reportTableData", [])
            except Exception:  # one flaky page/year shouldn't sink the rest
                break
            new = [r for r in rows if r.get("~id") not in seen]
            if not new:  # empty page or all-repeats → end of this year
                break
            seen.update(r.get("~id") for r in new)
            collected.extend(new)
    return _upsert_all(parse_chittorgarh_rows(collected), source="chittorgarh")


# ---- CSV import ------------------------------------------------------------

def _match_headers(fieldnames):
    normalized = {name.strip().lower(): name for name in fieldnames if name}
    return {field: next((normalized[a] for a in aliases if a in normalized), None)
            for field, aliases in HEADER_ALIASES.items()}


def parse_history_csv(path) -> list:
    """Parse an aggregator IPO CSV into history records via tolerant header
    matching. Needs at least a name/company column."""
    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"{path.name}: empty or not a CSV")
        mapping = _match_headers(reader.fieldnames)
        if not mapping["name"]:
            raise ValueError(f"{path.name}: no company/IPO-name column found "
                             f"(headers: {reader.fieldnames})")
        records = []
        for raw in reader:
            name = (raw.get(mapping["name"]) or "").strip()
            if not name:
                continue
            rec = {"name": name}
            rec["symbol"] = (raw.get(mapping["symbol"]) or "").strip() if mapping["symbol"] else ""
            rec["segment"] = _segment(raw.get(mapping["segment"])) if mapping["segment"] else None
            for field in _DATE_FIELDS:
                rec[field] = _date(raw.get(mapping[field])) if mapping[field] else None
            for field in _PRICE_FIELDS:
                rec[field] = _num(raw.get(mapping[field])) if mapping[field] else None
            records.append(rec)
    return records


def import_csv(path) -> dict:
    return _upsert_all(parse_history_csv(path), source="csv")


# ---- carry live-tracked subscription into history --------------------------

def carry_from_tracker(today: date = None) -> dict:
    """Once an IPO we tracked live has closed, fold its real subscription
    figures into history (we won't get them from NSE after the fact)."""
    today = today or date.today()
    records = []
    for i in store.load("ipos", default=[]):
        try:
            closed = datetime.strptime(i["close_date"], "%Y-%m-%d").date() < today
        except (KeyError, ValueError):
            continue
        if not closed:
            continue
        records.append({
            "name": i["name"], "symbol": "", "close_date": i["close_date"],
            "sub_total": i.get("sub_total") or None, "sub_qib": i.get("sub_qib") or None,
            "sub_nii": i.get("sub_nii") or None, "sub_retail": i.get("sub_retail") or None,
        })
    return _upsert_all(records, source="tracker")


# ---- read side -------------------------------------------------------------

def records() -> list:
    """All history rows with listing gain + listing year computed, newest
    listing (falling back to close) first."""
    out = []
    for rec in store.load(STORE_KEY, default={}).values():
        item = dict(rec)
        item["listing_gain_pct"] = _gain(rec)
        anchor = rec.get("listing_date") or rec.get("close_date") or ""
        item["year"] = int(anchor[:4]) if anchor[:4].isdigit() else None
        out.append(item)
    out.sort(key=lambda r: (r.get("listing_date") or r.get("close_date") or ""), reverse=True)
    return out

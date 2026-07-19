"""Fetch live IPO subscription numbers from NSE's public JSON endpoints.

NSE requires a browser-ish User-Agent and a cookie warm-up request before
the /api/* endpoints respond. Field names on these endpoints drift, so
parsing is deliberately tolerant: unknown shapes are skipped, never fatal.
Numbers land in the same store the manual CLI writes to, so recommend() and
reminders work identically either way.
"""

from datetime import datetime

try:
    import requests  # optional — live NSE/Upstox fetch only (requirements-invest.txt)
except ImportError:
    class _MissingRequests:
        def __getattr__(self, name):
            raise RuntimeError(
                "Live IPO sync needs the optional dependencies: "
                "pip install -r requirements-invest.txt")
    requests = _MissingRequests()

from . import ipo

NSE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/all-upcoming-issues-ipo",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(NSE, timeout=30)  # cookie warm-up
    return s


def _num(value) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _close_date(raw: str) -> str | None:
    """NSE dates come as '10-Jul-2026' or '10-07-2026'; normalize to ISO."""
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def fetch_current_issues() -> list:
    """Active IPOs with overall subscription, best-effort across field drift."""
    s = _session()
    resp = s.get(f"{NSE}/api/ipo-current-issue", timeout=30)
    resp.raise_for_status()
    data = resp.json()
    issues = data if isinstance(data, list) else data.get("data", [])
    out = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        name = item.get("companyName") or item.get("company") or item.get("symbol")
        close = _close_date(item.get("issueEndDate") or item.get("endDate") or "")
        if not name or not close:
            continue
        total = _num(item.get("noOfTime") or item.get("noOfTimesSubscribed")
                     or item.get("subscriptionTimes") or 0)
        if not total:  # fall back to computing it from shares bid vs offered
            offered = _num(item.get("noOfSharesOffered"))
            if offered:
                total = round(_num(item.get("noOfsharesBid") or item.get("noOfSharesBid")) / offered, 2)
        out.append({
            "name": str(name).strip(),
            "symbol": item.get("symbol", ""),
            "close_date": close,
            "sub_total": total,
            "series": item.get("series", ""),
        })
    return out


def fetch_category_subscription(symbol: str, session=None) -> dict:
    """QIB/NII/retail split for one active IPO, {} if the endpoint balks."""
    s = session or _session()
    try:
        resp = s.get(f"{NSE}/api/ipo-active-category?symbol={symbol}", timeout=30)
        resp.raise_for_status()
        rows = resp.json()
        rows = rows if isinstance(rows, list) else rows.get("dataList", rows.get("data", []))
        out = {}
        for row in rows:
            cat = str(row.get("category", "")).lower()
            times = _num(row.get("noOfTotalMeant"))
            if not times:  # NSE often leaves the ratio blank; compute it
                offered = _num(row.get("noOfShareOffered"))
                if offered:
                    times = round(_num(row.get("noOfSharesBid")) / offered, 2)
            # first match wins: main rows ("1","2","3") precede their subrows
            if ("qualified" in cat or "qib" in cat) and "sub_qib" not in out:
                out["sub_qib"] = times
            elif ("non institutional" in cat or "non-institutional" in cat) and "sub_nii" not in out:
                out["sub_nii"] = times
            elif "retail" in cat and "sub_retail" not in out:
                out["sub_retail"] = times
            elif cat.startswith("total") and "sub_total" not in out:
                out["sub_total"] = times
        return out
    except (requests.RequestException, ValueError):
        return {}


def refresh_from_upstox(account_id: str = "upstox-1",
                        statuses: tuple = ("open", "upcoming")) -> dict:
    """Populate the tracker from Upstox's IPO list API — the more reliable
    source (no cookie warm-up, no field drift) and it carries the exchange
    symbol, so an applied IPO's allotment matches its holding with no manual
    symbol entry. Per-status network failures are skipped, never fatal.

    Upstox reports only overall subscription (no QIB/NII/retail split); those
    reset to 0 here, so run the NSE refresh too if you want the category split.
    """
    from . import brokers
    updated = []
    for status in statuses:
        try:
            items = brokers.upstox_ipos(account_id, status=status)
        except Exception:  # Upstox flake on one status shouldn't sink the rest
            continue
        for item in items:
            name = item.get("name")
            close = item.get("bidding_end_date")
            if not name or not close:
                continue
            record = ipo.upsert(
                name, close,
                sub_total=_num(item.get("total_subscription")),
                notes=f"Upstox {item.get('issue_type', '')}".strip(),
                symbol=item.get("symbol") or None,  # None for not-yet-listed upcoming IPOs
            )
            updated.append(record["name"])
    return {"updated": updated, "count": len(updated)}


def refresh() -> dict:
    """Pull active NSE mainboard/SME IPOs and upsert them into the tracker."""
    issues = fetch_current_issues()
    s = _session()
    updated = []
    for issue in issues:
        cats = fetch_category_subscription(issue["symbol"], session=s) if issue["symbol"] else {}
        record = ipo.upsert(
            issue["name"], issue["close_date"],
            # NSE often leaves category ratios blank/zero — keep the best non-zero figure
            sub_total=cats.get("sub_total") or issue["sub_total"],
            sub_qib=cats.get("sub_qib") or 0.0,
            sub_nii=cats.get("sub_nii") or 0.0,
            sub_retail=cats.get("sub_retail") or 0.0,
            notes=f"NSE {issue['series']}".strip(),
            symbol=issue.get("symbol") or None,  # capture for later allotment matching
        )
        updated.append(record["name"])
    return {"updated": updated, "count": len(updated)}

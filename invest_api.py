"""Investments API — mounted by server.py under /api/invest/* and /auth/*.

Route names and behavior mirror the standalone investments dashboard this was
merged from; the web UI for these routes is public/invest.html (served at
/invest). Handlers return one of:

    ("json", status, payload)   – send payload as JSON with the given status
    ("redirect", location)      – 302
    None                        – not an invest route, caller 404s

GET  /api/invest/summary          totals per account / asset class
GET  /api/invest/signals          flagged positions (buy/sell/keep review list)
GET  /api/invest/bonds            bond positions with maturity/cashflow detail
GET  /api/invest/ipos             tracked IPOs with apply/skip calls (?closed=1)
GET  /api/invest/ipo-history      past listed IPOs: subscription, prices, gain
GET  /api/invest/ipo-allotments   applied IPOs: allotted?/awaiting + listing P&L
GET  /api/invest/accounts         account registry
GET  /api/invest/holdings/<id>    raw stored holding rows for one account (manual editor)
GET  /api/invest/persons          union of finances settings.persons + account owners
GET  /api/invest/tokens           broker API credential/token status
GET  /api/invest/imports          files waiting in imports/: {csv: [...], xlsx: [...]}
POST /api/invest/accounts         create account {id, broker_type, label, owner}
POST /api/invest/accounts/<id>    edit account label/owner {"label"?, "owner"?}
POST /api/invest/accounts/<id>/owner  set an account's owner {"owner": "Name"}
DELETE /api/invest/accounts/<id>  delete account (?force=1 to drop with holdings)
POST /api/invest/ipos             upsert IPO   {name, close_date, sub_total, ...}
POST /api/invest/ipos/applied     mark applied {name, applied?, symbol?, ...}
POST /api/invest/sync/ipos        refresh IPO subscription numbers from NSE
POST /api/invest/sync/ipos-upstox refresh tracker from Upstox IPO API
POST /api/invest/sync/ipo-history rebuild past-IPO history (NSE + Chittorgarh + tracker)
POST /api/invest/import-ipo-history  import a past-IPO CSV {file} (from imports/)
POST /api/invest/sync/<account>   pull holdings from broker API (needs fresh token)
POST /api/invest/import           import CSV   {account, file}
POST /api/invest/import-wint      import Wint Wealth xlsx {account, holding_file, ...}
POST /api/invest/manual           manual rows  {account, rows: [...]}
GET  /auth/<kite|upstox>/<account>/login      redirect to broker login
GET  /auth/<kite|upstox>/<account>/callback   token exchange + sync, then back to /invest
"""

import json

import config
from investlib import analysis, brokers, ipo, ipo_fetch, ipo_history, portfolio, store

API_PREFIX = "/api/invest"

# Finances (finances.json) access, injected by server.py at wiring time so
# this module stays importable/testable standalone (unit tests never wire
# these — the endpoints that need them just degrade to an empty persons list
# and a no-op append). get_persons() -> list[str]; add_person(name) -> None,
# expected to write through the server's locked finances.json path.
_get_persons = lambda: []          # noqa: E731
_add_person = lambda name: None    # noqa: E731


def set_finances_hooks(get_persons, add_person) -> None:
    global _get_persons, _add_person
    _get_persons = get_persons
    _add_person = add_person


def _import_path(filename: str):
    path = (config.IMPORTS_DIR / filename).resolve()
    if config.IMPORTS_DIR.resolve() not in path.parents:
        raise ValueError("file must be inside imports/")
    return path


def handle_get(path: str, query: dict):
    route = path.rstrip("/")
    if route.startswith("/auth/"):
        try:
            return _auth(route, query)
        except Exception as e:  # surfaced to the dashboard, this is a local tool
            return ("json", 500, {"error": str(e)})
    if not route.startswith(API_PREFIX + "/"):
        return None
    route = route[len(API_PREFIX):]
    try:
        if route == "/summary":
            return ("json", 200, analysis.summary())
        if route == "/signals":
            return ("json", 200, analysis.signals())
        if route == "/bonds":
            return ("json", 200, analysis.bonds())
        if route == "/ipos":
            include_closed = query.get("closed", ["0"])[0] == "1"
            return ("json", 200, ipo.tracked(include_closed=include_closed))
        if route == "/ipo-history":
            return ("json", 200, ipo_history.records())
        if route == "/ipo-allotments":
            return ("json", 200, ipo.allotment_status())
        if route == "/accounts":
            return ("json", 200, store.accounts())
        if route.startswith("/holdings/"):
            # raw stored rows for one account — used by the manual-holdings editor
            # to pre-fill existing rows for edit/remove (localhost, user's own data).
            account_id = route[len("/holdings/"):]
            snapshot = portfolio.all_holdings().get(account_id, {})
            return ("json", 200, {
                "account": account_id,
                "rows": snapshot.get("rows", []),
                "source": snapshot.get("source", ""),
                "as_of": snapshot.get("as_of", ""),
            })
        if route == "/persons":
            owners = {a.get("owner", "") for a in store.accounts() if a.get("owner")}
            persons = sorted(set(_get_persons()) | owners)
            return ("json", 200, {"persons": persons})
        if route == "/tokens":
            return ("json", 200, brokers.token_status())
        if route == "/imports":
            config.IMPORTS_DIR.mkdir(exist_ok=True)
            return ("json", 200, {
                "csv": sorted(p.name for p in config.IMPORTS_DIR.glob("*.csv")),
                "xlsx": sorted(p.name for p in config.IMPORTS_DIR.glob("*.xlsx")),
            })
        return ("json", 404, {"error": "not found"})
    except Exception as e:
        return ("json", 500, {"error": str(e)})


def _auth(route: str, query: dict):
    # /auth/<kind>/<account>/<login|callback>
    parts = route.strip("/").split("/")
    if len(parts) != 4:
        return ("json", 404, {"error": "bad auth path"})
    _, kind, account_id, step = parts
    if step == "login":
        return ("redirect", brokers.login_url(account_id))
    if step == "callback":
        if kind == "kite":
            brokers.kite_exchange(account_id, query["request_token"][0])
        elif kind == "upstox":
            brokers.upstox_exchange(account_id, query["code"][0])
        else:
            return ("json", 404, {"error": f"unknown broker {kind}"})
        brokers.sync(account_id)
        return ("redirect", "/invest")
    return ("json", 404, {"error": "not found"})


def _register_owner(owner: str) -> None:
    """Add a new owner name to the finances persons list (so it shows up as a
    suggestion everywhere). No-op for empty names or ones already known."""
    if owner and owner not in _get_persons():
        _add_person(owner)


def _create_account(data: dict):
    acct = store.create_account(
        data.get("id", ""), data.get("broker_type", ""),
        label=data.get("label", ""), owner=data.get("owner", ""),
        asset_class=(data.get("asset_class") or None))
    _register_owner(acct.get("owner", ""))
    return ("json", 200, acct)


def _update_account(account_id: str, data: dict):
    if "owner" in data and not isinstance(data["owner"], str):
        return ("json", 400, {"error": "owner must be a string"})
    if "label" in data and not isinstance(data["label"], str):
        return ("json", 400, {"error": "label must be a string"})
    acct = store.update_account(
        account_id,
        label=data["label"] if "label" in data else None,
        owner=data["owner"] if "owner" in data else None)
    _register_owner(acct.get("owner", ""))
    return ("json", 200, acct)


def _set_account_owner(route: str, data: dict):
    # route is "/accounts/<id>/owner"
    parts = route.strip("/").split("/")
    if len(parts) != 3:
        return ("json", 404, {"error": "not found"})
    account_id = parts[1]
    owner = data.get("owner")
    if not isinstance(owner, str):
        return ("json", 400, {"error": "owner must be a string"})
    accts = store.accounts()
    acct = next((a for a in accts if a.get("id") == account_id), None)
    if acct is None:
        return ("json", 404, {"error": f"unknown account: {account_id}"})
    owner = owner.strip()
    acct["owner"] = owner
    store.save("accounts", accts)
    _register_owner(owner)
    return ("json", 200, acct)


def handle_post(path: str, body: bytes):
    route = path.rstrip("/")
    if not route.startswith(API_PREFIX + "/"):
        return None
    route = route[len(API_PREFIX):]
    try:
        if route == "/sync/ipos":
            return ("json", 200, ipo_fetch.refresh())
        if route == "/sync/ipos-upstox":
            return ("json", 200, ipo_fetch.refresh_from_upstox())
        if route == "/sync/ipo-history":
            nse = ipo_history.fetch_from_nse()
            chittorgarh = ipo_history.fetch_from_chittorgarh()
            tracker = ipo_history.carry_from_tracker()
            return ("json", 200, {"nse": nse, "chittorgarh": chittorgarh,
                                  "tracker": tracker,
                                  "total": len(ipo_history.records())})
        if route.startswith("/sync/"):
            return ("json", 200, brokers.sync(route.rsplit("/", 1)[1]))
        data = json.loads(body or b"{}")
        if route == "/accounts":
            return _create_account(data)
        if route.startswith("/accounts/") and route.endswith("/owner"):
            return _set_account_owner(route, data)
        if route.startswith("/accounts/"):
            parts = route.strip("/").split("/")
            if len(parts) == 2:
                return _update_account(parts[1], data)
        if route == "/ipos":
            return ("json", 200, ipo.upsert(**data))
        if route == "/ipos/applied":
            return ("json", 200, ipo.set_applied(**data))
        if route == "/import-ipo-history":
            return ("json", 200, ipo_history.import_csv(_import_path(data["file"])))
        if route == "/import":
            return ("json", 200, portfolio.import_holdings(data["account"], _import_path(data["file"])))
        if route == "/import-wint":
            cashflow_file = data.get("cashflow_file") or None
            cashflow_path = _import_path(cashflow_file) if cashflow_file else None
            summary_file = data.get("summary_file") or None
            summary_path = _import_path(summary_file) if summary_file else None
            return ("json", 200, portfolio.import_wint_statement(
                data["account"], _import_path(data["holding_file"]), cashflow_path, summary_path))
        if route == "/manual":
            return ("json", 200, portfolio.set_manual_holdings(data["account"], data["rows"]))
        return ("json", 404, {"error": "not found"})
    except (ValueError, KeyError, TypeError, FileNotFoundError) as e:
        return ("json", 400, {"error": str(e)})
    except Exception as e:
        return ("json", 500, {"error": str(e)})


def handle_delete(path: str, query: dict):
    route = path.rstrip("/")
    if not route.startswith(API_PREFIX + "/"):
        return None
    route = route[len(API_PREFIX):]
    try:
        if route.startswith("/accounts/"):
            parts = route.strip("/").split("/")
            if len(parts) == 2:
                force = query.get("force", ["0"])[0] == "1"
                return ("json", 200, store.delete_account(parts[1], force=force))
        return ("json", 404, {"error": "not found"})
    except (ValueError, KeyError) as e:
        return ("json", 400, {"error": str(e)})
    except Exception as e:
        return ("json", 500, {"error": str(e)})

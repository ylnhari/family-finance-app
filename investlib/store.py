"""JSON file store under data/. One file per collection, no database."""

import json
import re
from pathlib import Path

import config
from atomic_write import atomic_write_json

# Broker types an account can be. These drive login/sync capability and the
# per-id .env var convention (kite/upstox log in; coin rides a kite session;
# wint/smallcase/manual are CSV/xlsx/manual only). NO personal data here — a
# fresh install starts with ZERO accounts and the user adds their own.
BROKER_TYPES = ("kite", "upstox", "coin", "wint", "smallcase", "manual")

# Per broker type: a display name + the default asset class for a new account.
# Generic, open-source defaults only; the user can override label/owner.
BROKER_META = {
    "kite":      {"broker": "Zerodha Kite",   "asset_class": "stocks"},
    "upstox":    {"broker": "Upstox",         "asset_class": "stocks"},
    "coin":      {"broker": "Coin (Zerodha)", "asset_class": "mutual_funds"},
    "wint":      {"broker": "Wint Wealth",    "asset_class": "bonds"},
    "smallcase": {"broker": "smallcase",      "asset_class": "managed_stocks"},
    "manual":    {"broker": "Manual",         "asset_class": "other"},
}

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*$")
ID_MAX_LEN = 40  # keep ids short enough that the Accounts table never overflows the page


def _path(collection: str) -> Path:
    return config.DATA_DIR / f"{collection}.json"


def load(collection: str, default=None):
    path = _path(collection)
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"data/invest/{collection}.json is corrupted ({e}). "
            "A .bak backup may exist next to it."
        ) from e


def save(collection: str, data) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_path(collection), data)


def _infer_broker_type(account_id: str) -> str:
    """Best-effort broker type from an id prefix — only used to backfill records
    saved before the broker_type field existed (migration for older installs)."""
    for t in ("kite", "upstox", "coin", "wint", "smallcase"):
        if (account_id or "").startswith(t):
            return t
    return "manual"


def accounts() -> list:
    """The account registry. A fresh install has NONE — accounts are user data,
    created via the dashboard (POST /api/invest/accounts), never seeded with
    example/personal accounts. Old records are backfilled with owner/broker_type
    so existing installs keep working unchanged."""
    accts = load("accounts", default=None)
    if accts is None:
        return []
    changed = False
    for a in accts:
        if "owner" not in a:
            a["owner"] = ""
            changed = True
        if not a.get("broker_type"):
            a["broker_type"] = _infer_broker_type(a.get("id", ""))
            changed = True
    if changed:
        save("accounts", accts)
    return accts


def create_account(account_id: str, broker_type: str, label: str = "",
                   owner: str = "", asset_class: str = None) -> dict:
    """Add a new account to the registry. Raises ValueError on a bad id, an
    unknown broker type, or a duplicate id."""
    account_id = (account_id or "").strip().lower()
    if not account_id:
        raise ValueError("account id is required")
    if not ID_RE.match(account_id):
        raise ValueError("account id must be lowercase letters, digits and hyphens (e.g. kite-1)")
    if len(account_id) > ID_MAX_LEN:
        raise ValueError(f"account id must be at most {ID_MAX_LEN} characters")
    if broker_type not in BROKER_TYPES:
        raise ValueError(f"unknown broker type '{broker_type}' — expected one of {', '.join(BROKER_TYPES)}")
    accts = accounts()
    if any(a.get("id") == account_id for a in accts):
        raise ValueError(f"account already exists: {account_id}")
    meta = BROKER_META[broker_type]
    acct = {
        "id": account_id,
        "broker": meta["broker"],
        "broker_type": broker_type,
        "asset_class": asset_class or meta["asset_class"],
        "label": (label or "").strip() or account_id,
        "owner": (owner or "").strip(),
    }
    accts.append(acct)
    save("accounts", accts)
    return acct


def update_account(account_id: str, *, label=None, owner=None) -> dict:
    """Edit an account's label and/or owner. Raises ValueError if unknown."""
    accts = accounts()
    acct = next((a for a in accts if a.get("id") == account_id), None)
    if acct is None:
        raise ValueError(f"unknown account: {account_id}")
    if label is not None:
        acct["label"] = str(label).strip() or acct["id"]
    if owner is not None:
        acct["owner"] = str(owner).strip()
    save("accounts", accts)
    return acct


def delete_account(account_id: str, *, force: bool = False) -> dict:
    """Remove an account. Blocks when the account still has holdings unless
    force=True (which also drops those holdings). Raises ValueError if unknown."""
    accts = accounts()
    acct = next((a for a in accts if a.get("id") == account_id), None)
    if acct is None:
        raise ValueError(f"unknown account: {account_id}")
    holdings = load("holdings", default={})
    has_holdings = bool(holdings.get(account_id, {}).get("rows"))
    if has_holdings and not force:
        raise ValueError(
            f"{account_id} still has holdings — remove them first or pass force=1 to delete anyway")
    save("accounts", [a for a in accts if a.get("id") != account_id])
    if force and account_id in holdings:
        del holdings[account_id]
        save("holdings", holdings)
    return {"deleted": account_id}

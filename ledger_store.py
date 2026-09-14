"""Narrow, local-only persistence for the lending and borrowing ledgers.

This module deliberately works through the finance app's existing lock, backup,
and atomic-write helpers.  It never exposes or rewrites the rest of the finance
document, which lets agent-facing callers make one guarded ledger change at a
time without needing a full-data snapshot.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation


MAX_IDENTIFIER_LENGTH = 160
MAX_PERSON_LENGTH = 160
MAX_INSTRUMENT_LENGTH = 240
MAX_NOTES_LENGTH = 4_000
MAX_RECORDS_PAGE = 100
DEFAULT_RECORDS_PAGE = 50
MAX_MONEY = Decimal("1000000000")

RECORD_FIELDS = (
    "date",
    "type",
    "instrument",
    "amount",
    "cashbackMode",
    "cashback",
    "extraCharges",
    "cancelled",
    "notes",
)

RECORD_DEFAULTS = {
    "instrument": "",
    "cashbackMode": "fixed",
    "cashback": 0,
    "extraCharges": 0,
    "cancelled": False,
    "notes": "",
}


class LedgerError(Exception):
    """A safe, user-facing ledger operation error."""


class LedgerValidationError(LedgerError):
    """The supplied tool input does not fit the ledger schema."""


class LedgerNotFoundError(LedgerError):
    """The requested ledger or record no longer exists."""


class LedgerConflictError(LedgerError):
    """A stale revision or conflicting idempotency key was supplied."""

    def __init__(self, message, current_revision=None):
        super().__init__(message)
        self.current_revision = current_revision


class LedgerDataError(LedgerError):
    """The stored finance document is not shaped like a ledger document."""


def _text(value, field, maximum, required=False):
    if not isinstance(value, str):
        raise LedgerValidationError("%s must be a string" % field)
    value = value.strip()
    if required and not value:
        raise LedgerValidationError("%s is required" % field)
    if len(value) > maximum:
        raise LedgerValidationError("%s is too long" % field)
    return value


def _identifier(value, field):
    return _text(value, field, MAX_IDENTIFIER_LENGTH, required=True)


def _idempotency_key(value):
    value = _text(value, "idempotency_key", 80, required=True)
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise LedgerValidationError("idempotency_key must be a UUID") from None


def _date(value):
    value = _text(value, "date", 10, required=True)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise LedgerValidationError("date must be YYYY-MM-DD") from None
    if parsed.strftime("%Y-%m-%d") != value:
        raise LedgerValidationError("date must be YYYY-MM-DD")
    return value


def _money(value, field, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise LedgerValidationError("%s must be a number" % field)
    if isinstance(value, float) and not math.isfinite(value):
        raise LedgerValidationError("%s must be finite" % field)
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise LedgerValidationError("%s must be a number" % field) from None
    if not amount.is_finite():
        raise LedgerValidationError("%s must be finite" % field)
    if amount > MAX_MONEY:
        raise LedgerValidationError("%s is too large" % field)
    if positive and amount <= 0:
        raise LedgerValidationError("%s must be greater than zero" % field)
    if not positive and amount < 0:
        raise LedgerValidationError("%s cannot be negative" % field)
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


def _record_values(raw, base=None):
    if not isinstance(raw, dict):
        raise LedgerValidationError("record must be an object")
    unknown = set(raw) - set(RECORD_FIELDS)
    if unknown:
        raise LedgerValidationError("record contains unsupported field: %s" % sorted(unknown)[0])

    if base is None:
        missing = [name for name in ("date", "type", "amount") if name not in raw]
        if missing:
            raise LedgerValidationError("record is missing required field: %s" % missing[0])
        source = dict(RECORD_DEFAULTS)
    else:
        if not raw:
            raise LedgerValidationError("patch must contain at least one field")
        source = {name: base.get(name, RECORD_DEFAULTS.get(name)) for name in RECORD_FIELDS}
    source.update(raw)

    record_type = source.get("type")
    if record_type not in ("debit", "credit"):
        raise LedgerValidationError("type must be debit or credit")
    cashback_mode = source.get("cashbackMode")
    if cashback_mode not in ("fixed", "percent"):
        raise LedgerValidationError("cashbackMode must be fixed or percent")
    cancelled = source.get("cancelled")
    if not isinstance(cancelled, bool):
        raise LedgerValidationError("cancelled must be true or false")

    cashback = _money(source.get("cashback"), "cashback")
    if cashback_mode == "percent" and cashback > 100:
        raise LedgerValidationError("percent cashback cannot exceed 100")

    return {
        "date": _date(source.get("date")),
        "type": record_type,
        "instrument": _text(source.get("instrument"), "instrument", MAX_INSTRUMENT_LENGTH),
        "amount": _money(source.get("amount"), "amount", positive=True),
        "cashbackMode": cashback_mode,
        "cashback": cashback,
        "extraCharges": _money(source.get("extraCharges"), "extraCharges"),
        "cancelled": cancelled,
        "notes": _text(source.get("notes"), "notes", MAX_NOTES_LENGTH),
    }


def _ledger_revision(ledger):
    """Return a stable opaque token for one ledger's current state."""
    payload = json.dumps(
        ledger, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _transactions(ledger, for_write=False):
    if "transactions" not in ledger:
        if for_write:
            ledger["transactions"] = []
        else:
            return []
    transactions = ledger.get("transactions")
    if not isinstance(transactions, list):
        raise LedgerDataError("ledger transactions are not a list")
    return transactions


def _record_public(record, include_notes=False):
    result = {
        "id": record.get("id", ""),
        "date": record.get("date", ""),
        "type": record.get("type", ""),
        "instrument": record.get("instrument", ""),
        "amount": record.get("amount", 0),
        "cashbackMode": record.get("cashbackMode", "fixed"),
        "cashback": record.get("cashback", 0),
        "extraCharges": record.get("extraCharges", 0),
        "cancelled": bool(record.get("cancelled", False)),
    }
    if include_notes:
        result["notes"] = record.get("notes", "")
    return result


def _same_record(record, candidate):
    return all(
        record.get(field, RECORD_DEFAULTS.get(field)) == candidate.get(field)
        for field in ("id",) + RECORD_FIELDS
    )


class LedgerStore:
    """A narrow ledger facade over an initialized ``server`` module.

    ``server_module`` is injected so callers share the app's exact file lock,
    daily-backup policy, and atomic writer without importing a second storage
    implementation.
    """

    def __init__(self, server_module):
        self._server = server_module

    def _load_document(self):
        try:
            with open(self._server.data_file(), "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except FileNotFoundError:
            raise LedgerDataError("finance data is not initialized") from None
        except json.JSONDecodeError:
            raise LedgerDataError("finance data is not valid JSON") from None
        if not isinstance(document, dict):
            raise LedgerDataError("finance data is not an object")
        return document

    @staticmethod
    def _ledgers(document, for_write=False):
        if "ledgers" not in document:
            if for_write:
                document["ledgers"] = []
            else:
                return []
        ledgers = document.get("ledgers")
        if not isinstance(ledgers, list):
            raise LedgerDataError("ledgers are not a list")
        return ledgers

    @staticmethod
    def _find_ledger(ledgers, ledger_id):
        for ledger in ledgers:
            if isinstance(ledger, dict) and ledger.get("id") == ledger_id:
                return ledger
        raise LedgerNotFoundError("ledger was not found")

    @staticmethod
    def _ledger_public(ledger):
        ledger_id = ledger.get("id")
        person = ledger.get("person")
        if not isinstance(ledger_id, str) or not isinstance(person, str):
            raise LedgerDataError("ledger is missing an id or person")
        return {
            "id": ledger_id,
            "person": person,
            "transaction_count": len(_transactions(ledger)),
            "revision": _ledger_revision(ledger),
        }

    def _read_snapshot(self):
        # Atomic replacement gives a reader either the old complete document
        # or the new complete document. Avoid taking the sidecar lock here so
        # a read-only MCP process never creates a lock file on a blank store.
        if not os.path.isfile(self._server.data_file()):
            raise LedgerDataError("finance data is not initialized")
        return self._load_document()

    def _mutate(self, callback):
        """Call ``callback(document)`` while locked, persisting only a change."""
        path = self._server.data_file()
        with self._server.file_lock(path):
            document = self._load_document()
            result, changed = callback(document)
            if changed:
                settings = document.setdefault("settings", {})
                if not isinstance(settings, dict):
                    raise LedgerDataError("settings are not an object")
                settings["lastUpdated"] = datetime.now().isoformat(timespec="seconds")
                self._server.make_backup()
                self._server.atomic_write_json(path, document)
            return result

    def list_ledgers(self):
        document = self._read_snapshot()
        ledgers = self._ledgers(document)
        return {"ledgers": [self._ledger_public(ledger) for ledger in ledgers if isinstance(ledger, dict)]}

    def list_records(self, ledger_id, cursor=None, limit=None, year=None, include_notes=False):
        ledger_id = _identifier(ledger_id, "ledger_id")
        if cursor is None:
            cursor = 0
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise LedgerValidationError("cursor must be a non-negative integer")
        if limit is None:
            limit = DEFAULT_RECORDS_PAGE
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RECORDS_PAGE:
            raise LedgerValidationError("limit must be between 1 and %d" % MAX_RECORDS_PAGE)
        if year is not None:
            if not isinstance(year, str) or len(year) != 4 or not year.isdigit():
                raise LedgerValidationError("year must be a four-digit string")
        if not isinstance(include_notes, bool):
            raise LedgerValidationError("include_notes must be true or false")

        document = self._read_snapshot()
        ledger = self._find_ledger(self._ledgers(document), ledger_id)
        records = _transactions(ledger)
        if year is not None:
            records = [record for record in records if str(record.get("date", "")).startswith(year + "-")]
        page = records[cursor:cursor + limit]
        next_cursor = cursor + len(page)
        if next_cursor >= len(records):
            next_cursor = None
        return {
            "ledger": self._ledger_public(ledger),
            "records": [_record_public(record, include_notes) for record in page if isinstance(record, dict)],
            "next_cursor": next_cursor,
        }

    def create_ledger(self, person, idempotency_key):
        person = _text(person, "person", MAX_PERSON_LENGTH, required=True)
        key = _idempotency_key(idempotency_key)

        def change(document):
            ledgers = self._ledgers(document, for_write=True)
            for ledger in ledgers:
                if not isinstance(ledger, dict):
                    continue
                if ledger.get("id") == key:
                    if ledger.get("person") != person:
                        raise LedgerConflictError("idempotency_key was already used for a different ledger")
                    return {"created": False, "ledger": self._ledger_public(ledger)}, False
                if isinstance(ledger.get("person"), str) and ledger["person"].casefold() == person.casefold():
                    raise LedgerConflictError("a ledger already exists for this person")
            ledger = {"id": key, "person": person, "transactions": []}
            ledgers.append(ledger)
            return {"created": True, "ledger": self._ledger_public(ledger)}, True

        return self._mutate(change)

    def create_record(self, ledger_id, record, idempotency_key):
        ledger_id = _identifier(ledger_id, "ledger_id")
        key = _idempotency_key(idempotency_key)
        values = _record_values(record)

        def change(document):
            ledger = self._find_ledger(self._ledgers(document, for_write=True), ledger_id)
            records = _transactions(ledger, for_write=True)
            candidate = {"id": key, **values}
            for existing in records:
                if isinstance(existing, dict) and existing.get("id") == key:
                    if not _same_record(existing, candidate):
                        raise LedgerConflictError("idempotency_key was already used for a different record")
                    return {
                        "created": False,
                        "ledger_id": ledger_id,
                        "record_id": key,
                        "revision": _ledger_revision(ledger),
                    }, False
            records.append(candidate)
            return {
                "created": True,
                "ledger_id": ledger_id,
                "record_id": key,
                "revision": _ledger_revision(ledger),
            }, True

        return self._mutate(change)

    def update_record(self, ledger_id, record_id, expected_revision, patch):
        ledger_id = _identifier(ledger_id, "ledger_id")
        record_id = _identifier(record_id, "record_id")
        expected_revision = _text(expected_revision, "expected_revision", 100, required=True)

        def change(document):
            ledger = self._find_ledger(self._ledgers(document, for_write=True), ledger_id)
            current_revision = _ledger_revision(ledger)
            if expected_revision != current_revision:
                raise LedgerConflictError("ledger changed; read it again before updating", current_revision)
            records = _transactions(ledger, for_write=True)
            for existing in records:
                if isinstance(existing, dict) and existing.get("id") == record_id:
                    existing.update(_record_values(patch, base=existing))
                    return {
                        "updated": True,
                        "ledger_id": ledger_id,
                        "record_id": record_id,
                        "revision": _ledger_revision(ledger),
                    }, True
            raise LedgerNotFoundError("record was not found")

        return self._mutate(change)

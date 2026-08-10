#!/usr/bin/env python3
"""Create a narrow, local card-only handoff for MyCard Benefits.

This program deliberately has no network or server dependency.  It scans a
Family Finance JSON document without decoding non-card top-level values, then
writes only the exact ``{schemaVersion, cards}`` shape accepted by MyCard
Benefits' ``mycard-vault --family-finance`` importer.  It never prints card
fields and refuses unknown card fields rather than silently carrying them.
"""

from __future__ import annotations

import argparse
import json
import math
import errno
import os
import stat
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_CARDS = 1_000
ROOT_KEYS = frozenset({"schemaVersion", "cards"})
CARD_FIELDS = frozenset({
    "id", "name", "bank", "owner", "type", "variant", "variantSubType", "number", "expiry",
    "cvv", "pin", "fees", "benefits", "lounge", "loungeCriteria", "status",
})
NETWORKS = frozenset({"visa", "mastercard", "master card", "amex", "rupay", "diners"})


class ExportRejected(ValueError):
    """Raised with a value-free message when a handoff is unsafe or unusable."""


PathIdentity = tuple[int, int, int]
PathSnapshot = tuple[tuple[Path, PathIdentity], ...]


def _identity(info: os.stat_result) -> PathIdentity:
    """A path identity that is meaningful for both POSIX and Windows NTFS."""
    return (info.st_dev, info.st_ino, getattr(info, "st_file_attributes", 0))


def _safe_lstat(path: Path) -> os.stat_result:
    """Stat one existing local item without following symlinks/reparse points."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ExportRejected("local path is unavailable") from exc
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    try:
        is_junction = getattr(path, "is_junction", lambda: False)()
    except OSError as exc:
        raise ExportRejected("local path is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or bool(reparse and info.st_file_attributes & reparse) or is_junction:
        raise ExportRejected("path must not use a symlink or junction")
    return info


def _snapshot_chain(path: Path) -> PathSnapshot:
    """Capture every existing ancestor so a later path swap is detectable."""
    candidate = path.expanduser().absolute()
    if not candidate.is_absolute():
        raise ExportRejected("path must be a local absolute path")
    parts = candidate.parts
    if not parts:
        raise ExportRejected("path must be a local absolute path")
    current = Path(parts[0])
    snapshot: list[tuple[Path, PathIdentity]] = [(current, _identity(_safe_lstat(current)))]
    for part in parts[1:]:
        current /= part
        snapshot.append((current, _identity(_safe_lstat(current))))
    return tuple(snapshot)


def _assert_chain_unchanged(snapshot: PathSnapshot) -> None:
    for path, expected in snapshot:
        if _identity(_safe_lstat(path)) != expected:
            raise ExportRejected("local path changed while opening")


def _regular_file(path: Path, *, output: bool = False) -> Path:
    candidate = path.expanduser().absolute()
    if output:
        _snapshot_chain(candidate.parent)
        try:
            _safe_lstat(candidate)
        except ExportRejected as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                # A present output (regular file, symlink, or junction) must
                # never be reused; do not make a best-effort overwrite.
                raise ExportRejected("output already exists; choose a new empty file") from exc
        else:
            raise ExportRejected("output already exists; choose a new empty file")
        if not candidate.parent.is_dir():
            raise ExportRejected("output folder is unavailable")
    else:
        _snapshot_chain(candidate.parent)
        info = _safe_lstat(candidate)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
            raise ExportRejected("source file is invalid or too large")
    return candidate


def _read_source(path: Path) -> str:
    safe = _regular_file(path)
    source_identity = _identity(_safe_lstat(safe))
    parent_snapshot = _snapshot_chain(safe.parent)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(safe, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != source_identity:
            raise ExportRejected("source changed while opening")
        _assert_chain_unchanged(parent_snapshot)
        if _identity(_safe_lstat(safe)) != source_identity:
            raise ExportRejected("source changed while opening")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            raw = handle.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise ExportRejected("source file cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ExportRejected("source file is too large")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExportRejected("source encoding is unsupported") from exc


def _space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _end_string(text: str, index: int) -> int:
    """Return the index after a JSON string without decoding its contents."""
    if index >= len(text) or text[index] != '"':
        raise ExportRejected("source JSON is invalid")
    index += 1
    while index < len(text):
        char = text[index]
        if char == '"':
            return index + 1
        if ord(char) < 0x20:
            raise ExportRejected("source JSON is invalid")
        if char == "\\":
            index += 1
            if index >= len(text) or text[index] not in '"\\/bfnrtu':
                raise ExportRejected("source JSON is invalid")
            if text[index] == "u":
                if index + 4 >= len(text) or any(c not in "0123456789abcdefABCDEF" for c in text[index + 1:index + 5]):
                    raise ExportRejected("source JSON is invalid")
                index += 4
        index += 1
    raise ExportRejected("source JSON is invalid")


def _end_value(text: str, index: int) -> int:
    """Lexically skip one JSON value without materialising private values."""
    index = _space(text, index)
    if index >= len(text):
        raise ExportRejected("source JSON is invalid")
    if text[index] == '"':
        return _end_string(text, index)
    if text[index] not in "[{":
        end = index
        while end < len(text) and text[end] not in ",}] \t\r\n":
            end += 1
        try:
            json.loads(text[index:end])
        except json.JSONDecodeError as exc:
            raise ExportRejected("source JSON is invalid") from exc
        return end

    opener, closer = text[index], "]" if text[index] == "[" else "}"
    stack = [closer]
    index += 1
    while index < len(text) and stack:
        char = text[index]
        if char == '"':
            index = _end_string(text, index)
            continue
        if char == "[":
            stack.append("]")
        elif char == "{":
            stack.append("}")
        elif char in "]}":
            if char != stack[-1]:
                raise ExportRejected("source JSON is invalid")
            stack.pop()
        index += 1
    if stack:
        raise ExportRejected("source JSON is invalid")
    return index


def _extract_cards_only(text: str) -> tuple[int, list[Any]]:
    """Decode only ``schemaVersion`` and ``cards`` from a full local document."""
    decoder = json.JSONDecoder()
    index = _space(text, 0)
    if index >= len(text) or text[index] != "{":
        raise ExportRejected("source JSON is invalid")
    index += 1
    found: dict[str, Any] = {}
    while True:
        index = _space(text, index)
        if index >= len(text):
            raise ExportRejected("source JSON is invalid")
        if text[index] == "}":
            index += 1
            break
        try:
            key, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            raise ExportRejected("source JSON is invalid") from exc
        if not isinstance(key, str) or key in found:
            raise ExportRejected("source JSON is invalid")
        index = _space(text, index)
        if index >= len(text) or text[index] != ":":
            raise ExportRejected("source JSON is invalid")
        index = _space(text, index + 1)
        if key in ROOT_KEYS:
            try:
                value, index = decoder.raw_decode(text, index)
            except json.JSONDecodeError as exc:
                raise ExportRejected("source JSON is invalid") from exc
            found[key] = value
        else:
            # This deliberately does not decode transactions, ledgers, or any
            # other root value into a Python object.
            index = _end_value(text, index)
        index = _space(text, index)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            index += 1
            break
        raise ExportRejected("source JSON is invalid")
    if _space(text, index) != len(text) or set(found) != ROOT_KEYS:
        raise ExportRejected("source is not a Family Finance card document")
    version, cards = found["schemaVersion"], found["cards"]
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 10_000:
        raise ExportRejected("source card schema version is invalid")
    if not isinstance(cards, list) or not cards or len(cards) > MAX_CARDS:
        raise ExportRejected("source has no usable cards")
    return version, cards


def _card_text(value: Any, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ExportRejected("card field is invalid")
    result = value.strip()
    if required and not result:
        raise ExportRejected("card field is invalid")
    if len(result) > 4096 or any(ord(char) < 32 for char in result):
        raise ExportRejected("card field is invalid")
    return result


def _validate_card(card: Any, seen_ids: set[str]) -> dict[str, Any]:
    if not isinstance(card, dict) or set(card) - CARD_FIELDS:
        raise ExportRejected("card shape is unsupported")
    if not {"id", "name", "number"} <= set(card):
        raise ExportRejected("card is incomplete")
    normalized: dict[str, Any] = {}
    for field, value in card.items():
        if field == "fees":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ExportRejected("card field is invalid")
            normalized[field] = value
            continue
        text = _card_text(value, required=field in {"id", "name", "number"})
        if text is not None:
            normalized[field] = text
    card_id = normalized["id"]
    if card_id in seen_ids:
        raise ExportRejected("duplicate card identifier")
    seen_ids.add(card_id)
    status = normalized.get("status", "")
    if status not in {"", "closed"}:
        raise ExportRejected("card status is unsupported")
    variant = normalized.get("variant", "")
    if variant and variant.casefold() not in NETWORKS:
        raise ExportRejected("card network is unsupported")
    expiry = normalized.get("expiry")
    if expiry:
        if len(expiry) != 10 or expiry[4] != "-" or expiry[7] != "-":
            raise ExportRejected("card expiry is invalid")
        try:
            parsed_expiry = date.fromisoformat(expiry)
        except ValueError as exc:
            raise ExportRejected("card expiry is invalid") from exc
        if not 1900 <= parsed_expiry.year <= 9999:
            raise ExportRejected("card expiry is invalid")
    return normalized


def build_card_only_export(text: str) -> dict[str, Any]:
    """Return only the importer-compatible card handoff; never log values."""
    version, cards = _extract_cards_only(text)
    seen_ids: set[str] = set()
    return {
        "schemaVersion": version,
        "cards": [_validate_card(card, seen_ids) for card in cards],
    }


def write_card_only_export(source: Path, output: Path) -> int:
    """Atomically create a new private handoff file and return its card count."""
    source = _regular_file(source)
    output = _regular_file(output, output=True)
    output_parent_snapshot = _snapshot_chain(output.parent)
    handoff = build_card_only_export(_read_source(source))
    payload = json.dumps(handoff, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    descriptor = None
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
        temporary = Path(temporary_name)
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass  # Windows ACLs are inherited; no data is printed either way.
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_identity = _identity(_safe_lstat(temporary))
        _assert_chain_unchanged(output_parent_snapshot)
        try:
            _safe_lstat(output)
        except ExportRejected as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                raise ExportRejected("output already exists; choose a new empty file") from exc
        else:
            raise ExportRejected("output already exists; choose a new empty file")
        if _identity(_safe_lstat(temporary)) != temporary_identity:
            raise ExportRejected("temporary output changed while finalizing")
        # Hard-linking creates the final name only when it did not already
        # exist.  Unlike os.replace(), it cannot race into overwriting an
        # unrelated sensitive handoff someone created after our first check.
        try:
            os.link(temporary, output)
        except FileExistsError as exc:
            raise ExportRejected("output already exists; choose a new empty file") from exc
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise ExportRejected("output already exists; choose a new empty file") from exc
            raise ExportRejected("card-only export could not be finalized") from exc
        temporary.unlink()
        temporary = None
    except OSError as exc:
        raise ExportRejected("card-only export could not be written") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return len(handoff["cards"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a local card-only MyCard Benefits handoff")
    parser.add_argument("--source", required=True, help="Family Finance JSON selected by the user")
    parser.add_argument("--output", required=True, help="new local JSON file for MyCard Benefits")
    args = parser.parse_args(argv)
    try:
        count = write_card_only_export(Path(args.source), Path(args.output))
    except ExportRejected as exc:
        print(f"Card-only export not created: {exc}", file=sys.stderr)
        return 2
    print(f"Created a local card-only export for {count} card record(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""A local stdio MCP server for the Family Finance lending ledger.

This intentionally is not an HTTP route. Codex starts it as a local child
process, and it returns only narrow ledger views rather than the full private
finance document.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

from ledger_store import (
    LedgerConflictError,
    LedgerError,
    LedgerStore,
)


SERVER_NAME = "family-finance-ledger"
SERVER_VERSION = "1.0.0"
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")

TOOL_ARGUMENTS = {
    "lending_list_ledgers": set(),
    "lending_list_records": {"ledger_id", "cursor", "limit", "year", "include_notes"},
    "lending_create_ledger": {"person", "idempotency_key"},
    "lending_create_record": {"ledger_id", "idempotency_key", "record"},
    "lending_update_record": {"ledger_id", "record_id", "expected_revision", "patch"},
}
TOOL_REQUIRED = {
    "lending_list_ledgers": set(),
    "lending_list_records": {"ledger_id"},
    "lending_create_ledger": {"person", "idempotency_key"},
    "lending_create_record": {"ledger_id", "idempotency_key", "record"},
    "lending_update_record": {"ledger_id", "record_id", "expected_revision", "patch"},
}

TOOLS = [
    {
        "name": "lending_list_ledgers",
        "description": "List ledger ids, people, record counts, and opaque revisions. It never returns records or notes.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "lending_list_records",
        "description": "List a bounded page of one person's ledger. Notes stay excluded unless include_notes is explicitly true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ledger_id": {"type": "string", "minLength": 1},
                "cursor": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "year": {"type": "string", "pattern": "^[0-9]{4}$"},
                "include_notes": {"type": "boolean"},
            },
            "required": ["ledger_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "lending_create_ledger",
        "description": "Create a person's ledger. The required UUID idempotency_key makes a retried create safe.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "person": {"type": "string", "minLength": 1, "maxLength": 160},
                "idempotency_key": {"type": "string", "format": "uuid"},
            },
            "required": ["person", "idempotency_key"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "lending_create_record",
        "description": "Append one lending debit or repayment credit. It never deletes a record; use cancellation through update for a void.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ledger_id": {"type": "string", "minLength": 1},
                "idempotency_key": {"type": "string", "format": "uuid"},
                "record": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                        "type": {"type": "string", "enum": ["debit", "credit"]},
                        "instrument": {"type": "string", "maxLength": 240},
                        "amount": {"type": "number", "exclusiveMinimum": 0},
                        "cashbackMode": {"type": "string", "enum": ["fixed", "percent"]},
                        "cashback": {"type": "number", "minimum": 0},
                        "extraCharges": {"type": "number", "minimum": 0},
                        "cancelled": {"type": "boolean"},
                        "notes": {"type": "string", "maxLength": 4000},
                    },
                    "required": ["date", "type", "amount"],
                    "additionalProperties": False,
                },
            },
            "required": ["ledger_id", "idempotency_key", "record"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "lending_update_record",
        "description": "Patch one record after reading the ledger revision. A stale revision is rejected instead of overwriting a newer change.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ledger_id": {"type": "string", "minLength": 1},
                "record_id": {"type": "string", "minLength": 1},
                "expected_revision": {"type": "string", "minLength": 1},
                "patch": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                        "type": {"type": "string", "enum": ["debit", "credit"]},
                        "instrument": {"type": "string", "maxLength": 240},
                        "amount": {"type": "number", "exclusiveMinimum": 0},
                        "cashbackMode": {"type": "string", "enum": ["fixed", "percent"]},
                        "cashback": {"type": "number", "minimum": 0},
                        "extraCharges": {"type": "number", "minimum": 0},
                        "cancelled": {"type": "boolean"},
                        "notes": {"type": "string", "maxLength": 4000},
                    },
                    "minProperties": 1,
                    "additionalProperties": False,
                },
            },
            "required": ["ledger_id", "record_id", "expected_revision", "patch"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
]


def _jsonrpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_result(payload, is_error=False):
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    else:
        result["structuredContent"] = payload
    return result


class MCPServer:
    def __init__(self, store, write_enabled):
        self._store = store
        self._write_enabled = write_enabled
        self._initialized = False

    def _tool_call(self, arguments):
        if not isinstance(arguments, dict):
            return _tool_result({"error": "tool arguments must be an object"}, is_error=True)
        name = arguments.get("name")
        values = arguments.get("arguments", {})
        if not isinstance(values, dict):
            return _tool_result({"error": "tool arguments must be an object"}, is_error=True)
        if name not in TOOL_ARGUMENTS:
            return _tool_result({"error": "unknown tool"}, is_error=True)
        unexpected = set(values) - TOOL_ARGUMENTS[name]
        if unexpected:
            return _tool_result(
                {"error": "unsupported tool argument: %s" % sorted(unexpected)[0]}, is_error=True
            )
        missing = TOOL_REQUIRED[name] - set(values)
        if missing:
            return _tool_result(
                {"error": "missing required tool argument: %s" % sorted(missing)[0]}, is_error=True
            )
        try:
            if name == "lending_list_ledgers":
                result = self._store.list_ledgers()
            elif name == "lending_list_records":
                result = self._store.list_records(
                    values.get("ledger_id"),
                    cursor=values.get("cursor"),
                    limit=values.get("limit"),
                    year=values.get("year"),
                    include_notes=values.get("include_notes", False),
                )
            elif name in ("lending_create_ledger", "lending_create_record", "lending_update_record"):
                if not self._write_enabled:
                    return _tool_result(
                        {"error": "This MCP server is read-only. Restart it with --write-enabled to make ledger changes."},
                        is_error=True,
                    )
                if name == "lending_create_ledger":
                    result = self._store.create_ledger(values.get("person"), values.get("idempotency_key"))
                elif name == "lending_create_record":
                    result = self._store.create_record(
                        values.get("ledger_id"), values.get("record"), values.get("idempotency_key")
                    )
                else:
                    result = self._store.update_record(
                        values.get("ledger_id"),
                        values.get("record_id"),
                        values.get("expected_revision"),
                        values.get("patch"),
                    )
        except LedgerConflictError as exc:
            payload = {"error": str(exc), "kind": "conflict"}
            if exc.current_revision:
                payload["current_revision"] = exc.current_revision
            return _tool_result(payload, is_error=True)
        except LedgerError as exc:
            return _tool_result({"error": str(exc)}, is_error=True)
        return _tool_result(result)

    def handle(self, request):
        if not isinstance(request, dict):
            return _jsonrpc_error(None, -32600, "Invalid Request")
        if request.get("jsonrpc") != "2.0":
            return _jsonrpc_error(None, -32600, "Invalid Request")
        request_id = request.get("id")
        is_notification = "id" not in request
        method = request.get("method")
        if not isinstance(method, str):
            return _jsonrpc_error(request_id if not is_notification else None, -32600, "Invalid Request")
        if not is_notification and (isinstance(request_id, bool) or not isinstance(request_id, (str, int, type(None)))):
            return _jsonrpc_error(None, -32600, "Invalid Request")
        params = request.get("params", {})
        if not isinstance(params, dict):
            return _jsonrpc_error(request_id if not is_notification else None, -32602, "Invalid params")
        if method == "initialize":
            requested = params.get("protocolVersion")
            protocol = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[-1]
            result = {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
            self._initialized = True
        elif method == "notifications/initialized":
            if not is_notification:
                return _jsonrpc_error(request_id, -32600, "Invalid Request")
            return None
        elif method == "ping":
            result = {}
        elif not self._initialized:
            if is_notification:
                return None
            return _jsonrpc_error(request_id, -32002, "Server not initialized")
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = self._tool_call(params)
        else:
            if is_notification:
                return None
            return _jsonrpc_error(request_id, -32601, "Method not found")
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve_stdio(server):
    """Serve newline-delimited JSON-RPC without ever logging request bodies."""
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            response = _jsonrpc_error(None, -32700, "Parse error")
        else:
            response = server.handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local MCP server for Family Finance lending ledgers")
    parser.add_argument("--data-dir", help="local finance data directory; defaults to the app's normal data directory")
    parser.add_argument("--write-enabled", action="store_true", help="allow create and patch tools")
    args = parser.parse_args(argv)

    # The MCP server does not use AI or broker configuration. Avoid loading a
    # local .env as an incidental side effect of importing the app module.
    os.environ["FF_NO_DOTENV"] = "1"
    import server as finance_server

    finance_server.DATA_DIR = os.path.abspath(args.data_dir or finance_server.DEFAULT_DATA_DIR)
    # A read-only invocation must not create a file, directory, or sidecar on
    # a blank checkout. An explicitly write-enabled session may initialize the
    # normal empty local store before its first create operation.
    if args.write_enabled:
        with contextlib.redirect_stdout(sys.stderr):
            finance_server.ensure_data()
    serve_stdio(MCPServer(LedgerStore(finance_server), args.write_enabled))


if __name__ == "__main__":
    main()

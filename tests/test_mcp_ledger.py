"""Black-box tests for the local stdio lending-ledger MCP server."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP = os.path.join(ROOT, "mcp_ledger_server.py")
SERVER = os.path.join(ROOT, "server.py")


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _http_json(method, url, body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = dict(headers or {})
    if data:
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8")), response.headers
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8")), error.headers


class MCPClient:
    def __init__(self, data_dir, write_enabled=False):
        env = dict(os.environ)
        env["FF_NO_DOTENV"] = "1"
        args = [sys.executable, MCP, "--data-dir", data_dir]
        if write_enabled:
            args.append("--write-enabled")
        self.proc = subprocess.Popen(
            args,
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._next_id = 1

    def request(self, method, params=None):
        request_id = self._next_id
        self._next_id += 1
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            stderr = self.proc.stderr.read()
            raise AssertionError("MCP server exited without a response: %s" % stderr)
        response = json.loads(line)
        if response.get("id") != request_id:
            raise AssertionError("MCP response id mismatch")
        return response

    def raw(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        response = json.loads(self.proc.stdout.readline())
        return response

    def initialize(self):
        return self.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})

    def call(self, name, arguments=None):
        response = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return response["result"]

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc.stdout.close()
        self.proc.stderr.close()


class LedgerMCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ff-mcp-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, write_enabled=False):
        client = MCPClient(self.tmp, write_enabled=write_enabled)
        self.addCleanup(client.close)
        return client

    @staticmethod
    def payload(result):
        return json.loads(result["content"][0]["text"])

    def test_initialize_lists_narrow_tools_without_protocol_noise(self):
        client = self.start()
        initialized = client.initialize()
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-03-26")
        listed = client.request("tools/list")
        tools = listed["result"]["tools"]
        self.assertEqual(
            {tool["name"] for tool in tools},
            {
                "lending_list_ledgers",
                "lending_list_records",
                "lending_create_ledger",
                "lending_create_record",
                "lending_update_record",
            },
        )
        self.assertFalse(any("data" in tool["name"] for tool in tools))

    def test_read_only_never_initializes_a_blank_store(self):
        client = self.start()
        client.initialize()
        refused = client.call("lending_create_ledger", {
            "person": "Demo Person",
            "idempotency_key": str(uuid.uuid4()),
        })
        self.assertTrue(refused["isError"])
        self.assertIn("read-only", self.payload(refused)["error"])
        uninitialized = client.call("lending_list_ledgers")
        self.assertTrue(uninitialized["isError"])
        self.assertIn("not initialized", self.payload(uninitialized)["error"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "finances.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "finances.json.lock")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "backups")))

    def test_create_is_idempotent_notes_are_opt_in_and_stale_updates_fail(self):
        client = self.start(write_enabled=True)
        client.initialize()
        ledger_key = str(uuid.uuid4())
        created = self.payload(client.call("lending_create_ledger", {
            "person": "Demo Person",
            "idempotency_key": ledger_key,
        }))
        self.assertTrue(created["created"])
        ledger_id = created["ledger"]["id"]
        repeated = self.payload(client.call("lending_create_ledger", {
            "person": "Demo Person",
            "idempotency_key": ledger_key,
        }))
        self.assertFalse(repeated["created"])

        record_key = str(uuid.uuid4())
        record = {
            "date": "2026-09-14",
            "type": "credit",
            "instrument": "Demo Savings",
            "amount": 15000,
            "notes": "private test note",
        }
        added = self.payload(client.call("lending_create_record", {
            "ledger_id": ledger_id,
            "idempotency_key": record_key,
            "record": record,
        }))
        self.assertTrue(added["created"])
        repeated_record = self.payload(client.call("lending_create_record", {
            "ledger_id": ledger_id,
            "idempotency_key": record_key,
            "record": record,
        }))
        self.assertFalse(repeated_record["created"])

        without_notes = self.payload(client.call("lending_list_records", {"ledger_id": ledger_id}))
        self.assertNotIn("notes", without_notes["records"][0])
        with_notes = self.payload(client.call("lending_list_records", {
            "ledger_id": ledger_id,
            "include_notes": True,
        }))
        self.assertEqual(with_notes["records"][0]["notes"], "private test note")

        revision = without_notes["ledger"]["revision"]
        updated = self.payload(client.call("lending_update_record", {
            "ledger_id": ledger_id,
            "record_id": record_key,
            "expected_revision": revision,
            "patch": {"cancelled": True},
        }))
        self.assertTrue(updated["updated"])
        self.assertNotEqual(updated["revision"], revision)
        stale = client.call("lending_update_record", {
            "ledger_id": ledger_id,
            "record_id": record_key,
            "expected_revision": revision,
            "patch": {"cancelled": False},
        })
        self.assertTrue(stale["isError"])
        self.assertEqual(self.payload(stale)["kind"], "conflict")

    def test_schema_errors_and_malformed_json_rpc_are_safe(self):
        client = self.start(write_enabled=True)
        before_init = client.request("tools/list")
        self.assertEqual(before_init["error"]["code"], -32002)
        client.initialize()
        malformed = client.raw("this is not json")
        self.assertEqual(malformed["error"]["code"], -32700)
        wrong_version = client.raw(json.dumps({"jsonrpc": "1.0", "id": 99, "method": "ping"}))
        self.assertEqual(wrong_version["error"]["code"], -32600)
        malformed_notification = client.request("notifications/initialized")
        self.assertEqual(malformed_notification["error"]["code"], -32600)
        unknown_argument = client.call("lending_list_ledgers", {"include_notes": True})
        self.assertTrue(unknown_argument["isError"])
        self.assertIn("unsupported", self.payload(unknown_argument)["error"])
        bad = client.call("lending_create_ledger", {
            "person": "Demo Person",
            "idempotency_key": "not-a-uuid",
        })
        self.assertTrue(bad["isError"])
        self.assertIn("UUID", self.payload(bad)["error"])

    def test_bounded_pagination_and_two_process_writes_keep_both_records(self):
        first = self.start(write_enabled=True)
        first.initialize()
        ledger_id = self.payload(first.call("lending_create_ledger", {
            "person": "Concurrent Demo",
            "idempotency_key": str(uuid.uuid4()),
        }))["ledger"]["id"]
        second = self.start(write_enabled=True)
        second.initialize()
        errors = []

        def add(client, date):
            result = client.call("lending_create_record", {
                "ledger_id": ledger_id,
                "idempotency_key": str(uuid.uuid4()),
                "record": {"date": date, "type": "debit", "amount": 100},
            })
            if result.get("isError"):
                errors.append(self.payload(result))

        threads = [
            threading.Thread(target=add, args=(first, "2026-01-01")),
            threading.Thread(target=add, args=(second, "2026-01-02")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        first_page = self.payload(first.call("lending_list_records", {
            "ledger_id": ledger_id, "limit": 1, "year": "2026",
        }))
        self.assertEqual(len(first_page["records"]), 1)
        self.assertEqual(first_page["next_cursor"], 1)
        second_page = self.payload(first.call("lending_list_records", {
            "ledger_id": ledger_id, "limit": 1, "cursor": first_page["next_cursor"], "year": "2026",
        }))
        self.assertEqual(len(second_page["records"]), 1)
        self.assertIsNone(second_page["next_cursor"])
        invalid_limit = first.call("lending_list_records", {"ledger_id": ledger_id, "limit": 101})
        self.assertTrue(invalid_limit["isError"])
        self.assertIn("between", self.payload(invalid_limit)["error"])


class BrowserMCPRevisionTests(unittest.TestCase):
    """A stale browser snapshot must not erase a later narrow MCP write."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ff-mcp-http-")
        self.port = _free_port()
        self.base = "http://127.0.0.1:%d" % self.port
        env = dict(os.environ)
        env["FF_NO_DOTENV"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, SERVER, "--port", str(self.port), "--data-dir", self.tmp, "--no-browser"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(50):
            try:
                _http_json("GET", self.base + "/api/data")
                break
            except Exception:
                time.sleep(0.1)
        else:
            output = self.proc.stdout.read()
            raise AssertionError("finance server did not start: %s" % output)
        self.mcp = MCPClient(self.tmp, write_enabled=True)
        self.mcp.initialize()

    def tearDown(self):
        self.mcp.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.proc.stdout.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stale_browser_put_cannot_erase_mcp_create(self):
        status, stale_document, headers = _http_json("GET", self.base + "/api/data")
        self.assertEqual(status, 200)
        stale_etag = headers.get("ETag")
        self.assertTrue(stale_etag)

        created = json.loads(self.mcp.call("lending_create_ledger", {
            "person": "MCP Demo Person",
            "idempotency_key": str(uuid.uuid4()),
        })["content"][0]["text"])
        self.assertTrue(created["created"])

        status, body, _ = _http_json(
            "PUT", self.base + "/api/data", stale_document, {"If-Match": stale_etag}
        )
        self.assertEqual(status, 409)
        self.assertIn("changed", body["error"])
        ledgers = json.loads(self.mcp.call("lending_list_ledgers")["content"][0]["text"])["ledgers"]
        self.assertTrue(any(ledger["id"] == created["ledger"]["id"] for ledger in ledgers))


if __name__ == "__main__":
    unittest.main()

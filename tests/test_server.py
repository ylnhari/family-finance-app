"""Server API + persistence tests. Zero dependencies (stdlib unittest).

Run from the project root:
    python -m unittest discover -s tests -p "test_*.py" -v

Boots a real server on a throwaway port + temp data dir, hits it over HTTP.
Gemini endpoints are NOT exercised (no quota burn / no network) — they're only
checked for graceful "key missing" behaviour.
"""
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
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server.py")
SAMPLE = os.path.join(ROOT, "samples", "demo-finances.json")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _raw(method, url, body=None, ctype="application/octet-stream"):
    """Request returning (status, raw_bytes, content_type) — for non-JSON responses."""
    r = urllib.request.Request(url, data=body, method=method,
                               headers={"Content-Type": ctype} if body else {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Blocks urllib's automatic redirect-following so OAuth login/callback
    tests can inspect a 302's Location header directly. The mocked broker
    URLs used in those tests are fake and must never actually be fetched."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _req_no_redirect(url):
    """GET url without following a 3xx. Returns (status, location_or_None, json_or_None)."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url, timeout=10) as resp:
            return resp.status, resp.headers.get("Location"), None
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        finally:
            e.close()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = None
        return e.code, e.headers.get("Location"), payload


def _boot_inprocess_server(data_dir, port):
    """Boot the real server INSIDE this test process (not a subprocess) so
    investlib.brokers' network-calling functions can be replaced with
    unittest.mock.patch.object — the only way to exercise the OAuth/sync HTTP
    handlers without ever touching a real broker network. Mirrors what
    server.main() wires up, minus argv parsing / browser-opening / sys.exit.

    NOTE: unlike the subprocess-booted classes above, this process may
    already have a real .env loaded into os.environ (config._load_env() runs
    once, the first time any test module imports `config`/`investlib`, and
    neither test.bat/test.sh nor `python -m unittest discover` set
    FF_NO_DOTENV for the top-level run) — so every account id used against a
    server booted this way MUST be an obviously-fake id that cannot collide
    with a real "<ID>_API_KEY" style env var name.
    """
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import server as srv
    import invest_api as iapi
    import config as invest_config
    srv.DATA_DIR = os.path.abspath(data_dir)
    srv.ensure_data()
    iapi.set_finances_hooks(srv._finances_persons, srv._finances_add_person)
    invest_config.DATA_DIR = Path(srv.DATA_DIR) / "invest"
    invest_config.IMPORTS_DIR = Path(srv.DATA_DIR) / "imports"
    invest_config.RUNTIME_PORT = port
    httpd = srv.FFServer(("127.0.0.1", port), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return srv, httpd, thread


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fft-")
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        # run without GEMINI_API_KEY so AI endpoints take the graceful-degrade path
        env = dict(os.environ)
        env.pop("GEMINI_API_KEY", None)
        env["FF_NO_DOTENV"] = "1"  # a real .env must not re-inject keys
        cls.proc = subprocess.Popen(
            [sys.executable, SERVER, "--port", str(cls.port),
             "--data-dir", cls.tmp, "--no-browser"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        cls._wait_ready()
        # The registry ships empty now (accounts are user data) — create the
        # accounts these tests exercise. kite-1 = stocks (manual/summary tests),
        # wint-2 = a bond scratch account (owner + live-row bridge tests).
        for spec in ({"id": "kite-1", "broker_type": "kite"},
                     {"id": "wint-2", "broker_type": "wint"}):
            _req("POST", cls.base + "/api/invest/accounts", spec)

    @classmethod
    def _wait_ready(cls):
        for _ in range(50):
            try:
                with urllib.request.urlopen(cls.base + "/api/data", timeout=1):
                    return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_get_data_returns_json_object(self):
        st, body = _req("GET", self.base + "/api/data")
        self.assertEqual(st, 200)
        self.assertIsInstance(body, dict)
        self.assertIn("settings", body)  # always written on save

    def test_ping_reports_app_and_data_dir(self):
        st, body = _req("GET", self.base + "/api/ping")
        self.assertEqual(st, 200)
        self.assertEqual(body["app"], "family-finance-app")
        self.assertEqual(body["dataDir"], os.path.abspath(self.tmp))

    def test_app_already_running_matches_only_same_data_dir(self):
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import server as srv
        # same app + same data dir -> reuse
        self.assertTrue(srv.app_already_running(self.port, self.tmp))
        # same app, different data dir (a --demo / --data-dir instance) -> NOT reuse
        self.assertFalse(srv.app_already_running(self.port, self.tmp + "_other"))
        # nothing listening -> False
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        self.assertFalse(srv.app_already_running(free, self.tmp))

    def test_file_lock_is_exclusive(self):
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import server as srv
        base = os.path.join(self.tmp, "lk")
        held = srv.file_lock(base, timeout=10)
        held.__enter__()
        try:
            with self.assertRaises(TimeoutError):
                srv.file_lock(base, timeout=0.3).__enter__()
        finally:
            held.__exit__(None, None, None)

    def test_occupied_port_fails_without_hopping(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy = blocker.getsockname()[1]
        try:
            env = dict(os.environ)
            env.pop("GEMINI_API_KEY", None)
            env["FF_NO_DOTENV"] = "1"  # a real .env must not re-inject keys
            proc = subprocess.run(
                [sys.executable, SERVER, "--port", str(busy),
                 "--data-dir", tempfile.mkdtemp(prefix="fft-busy-"), "--no-browser"],
                capture_output=True, text=True, timeout=20, env=env)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("unavailable", (proc.stdout + proc.stderr).lower())
        finally:
            blocker.close()

    def test_full_document_roundtrip_preserves_all_sections(self):
        doc = {k: [] for k in ("expenses", "monthlyInvestments", "portfolio",
                               "gold", "loans", "goals", "cards", "documents")}
        doc["settings"] = {"appName": "RT"}
        doc["income"] = {"persons": []}
        st, _ = _req("PUT", self.base + "/api/data", doc)
        self.assertEqual(st, 200)
        st, got = _req("GET", self.base + "/api/data")
        for key in ("settings", "income", "expenses", "loans", "portfolio", "gold", "goals", "cards"):
            self.assertIn(key, got)

    def test_put_roundtrip_persists(self):
        payload = {"settings": {}, "loans": [], "expenses": [], "marker": "hello-123"}
        st, body = _req("PUT", self.base + "/api/data", payload)
        self.assertEqual(st, 200)
        self.assertTrue(body["ok"])
        st, got = _req("GET", self.base + "/api/data")
        self.assertEqual(got.get("marker"), "hello-123")
        self.assertIn("lastUpdated", got["settings"])

    def test_concurrent_puts_no_permission_error(self):
        """Regression: ThreadingHTTPServer used to collide on finances.json.tmp (Errno 13)."""
        errors = []

        def writer(n):
            try:
                st, _ = _req("PUT", self.base + "/api/data",
                             {"settings": {}, "loans": [], "n": n})
                if st != 200:
                    errors.append(st)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], "concurrent PUTs should all succeed")
        # file must still be valid JSON after the storm
        st, got = _req("GET", self.base + "/api/data")
        self.assertEqual(st, 200)
        self.assertIn("n", got)

    def test_sample_data_is_valid_and_loadable(self):
        """The committed demo file must be valid and accepted by the server."""
        self.assertTrue(os.path.isfile(SAMPLE), "samples/demo-finances.json missing")
        with open(SAMPLE, "r", encoding="utf-8") as f:
            sample = json.load(f)
        st, body = _req("PUT", self.base + "/api/data", sample)
        self.assertEqual(st, 200)
        st, got = _req("GET", self.base + "/api/data")
        self.assertTrue(got["income"]["persons"], "sample should have earners")
        self.assertTrue(got["loans"], "sample should have loans")
        self.assertTrue(any(L.get("prepayments") for L in got["loans"]),
                        "sample should demo a loan prepayment")

    def test_backup_and_list(self):
        _req("PUT", self.base + "/api/data", {"settings": {}, "loans": []})
        st, body = _req("POST", self.base + "/api/backup")
        self.assertEqual(st, 200)
        self.assertTrue(body["name"].endswith(".json"))
        st, items = _req("GET", self.base + "/api/backups")
        self.assertTrue(any(b["name"] == body["name"] for b in items))

    def test_gemini_status_degrades_without_key(self):
        st, body = _req("GET", self.base + "/api/gemini-status")
        self.assertEqual(st, 200)
        self.assertFalse(body["available"])  # key was stripped from env

    def test_extract_payslip_without_key_is_503(self):
        st, body = _req("POST", self.base + "/api/extract-payslip", {"filename": "x.pdf"})
        self.assertEqual(st, 503)
        self.assertIn("error", body)

    def test_fetch_goal_price_without_key_is_503(self):
        st, body = _req("POST", self.base + "/api/fetch-goal-price", {"name": "Honda City"})
        self.assertEqual(st, 503)

    def test_fetch_gold_price_without_key_is_503(self):
        st, body = _req("POST", self.base + "/api/fetch-gold-price", {})
        self.assertEqual(st, 503)

    # ---- static serving --------------------------------------------------

    def test_serves_index_html(self):
        st, body, ctype = _raw("GET", self.base + "/")
        self.assertEqual(st, 200)
        self.assertIn(b"<div id=\"app\">", body)
        self.assertIn("text/html", ctype)

    def test_serves_finance_math_js(self):
        st, body, ctype = _raw("GET", self.base + "/finance-math.js")
        self.assertEqual(st, 200)
        self.assertIn(b"function loanState", body)
        self.assertIn("javascript", ctype)

    def test_shared_theme_controller_wired_into_both_pages(self):
        # D11: one theme source of truth — theme.js served + referenced by both pages
        st, body, ctype = _raw("GET", self.base + "/theme.js")
        self.assertEqual(st, 200)
        self.assertIn("javascript", ctype)
        self.assertIn(b"ffa_theme", body)          # the single shared key
        for page in ("/", "/invest"):
            _, html, _ = _raw("GET", self.base + page)
            self.assertIn(b"theme.js", html)

    # ---- file upload lifecycle ------------------------------------------

    def test_upload_list_download_delete_file(self):
        content = b"hello statement pdf bytes"
        st, body, _ = _raw("POST", self.base + "/api/upload?name=stmt.txt", content)
        self.assertEqual(st, 200)
        name = json.loads(body)["name"]
        # appears in listing
        st, files = _req("GET", self.base + "/api/files")
        self.assertTrue(any(f["name"] == name for f in files))
        # downloadable with same bytes
        st, got, _ = _raw("GET", self.base + "/files/" + name)
        self.assertEqual(got, content)
        # deletable
        st, _ = _req("DELETE", self.base + "/api/files/" + name)
        self.assertEqual(st, 200)
        st, files = _req("GET", self.base + "/api/files")
        self.assertFalse(any(f["name"] == name for f in files))

    def test_upload_dedupes_repeated_names(self):
        _raw("POST", self.base + "/api/upload?name=dup.txt", b"one")
        st, body, _ = _raw("POST", self.base + "/api/upload?name=dup.txt", b"two")
        self.assertNotEqual(json.loads(body)["name"], "dup.txt")  # second gets a suffix

    def test_empty_upload_rejected(self):
        st, body, _ = _raw("POST", self.base + "/api/upload?name=empty.txt", b"")
        self.assertEqual(st, 400)

    # ---- security / error paths -----------------------------------------

    def test_path_traversal_on_file_get_is_blocked(self):
        # safe_name() should neutralise the traversal — never leak finances.json
        st, body, _ = _raw("GET", self.base + "/files/..%2f..%2ffinances.json")
        self.assertIn(st, (400, 403, 404))
        self.assertNotIn(b"schemaVersion", body)

    def test_unknown_api_route_404(self):
        st, body = _req("GET", self.base + "/api/does-not-exist")
        self.assertEqual(st, 404)

    def test_put_invalid_json_rejected(self):
        st, body, _ = _raw("PUT", self.base + "/api/data", b"{not valid json", "application/json")
        self.assertEqual(st, 400)

    def test_delete_missing_file_404(self):
        st, body = _req("DELETE", self.base + "/api/files/nope-not-here.txt")
        self.assertEqual(st, 404)

    # ---- backup / restore ------------------------------------------------

    def test_restore_roundtrip(self):
        _req("PUT", self.base + "/api/data", {"settings": {}, "loans": [], "tag": "before"})
        st, b = _req("POST", self.base + "/api/backup")
        snap = b["name"]
        _req("PUT", self.base + "/api/data", {"settings": {}, "loans": [], "tag": "after"})
        st, _ = _req("POST", self.base + "/api/restore?name=" + snap)
        self.assertEqual(st, 200)
        st, got = _req("GET", self.base + "/api/data")
        self.assertEqual(got.get("tag"), "before")

    def test_restore_missing_backup_404(self):
        st, body = _req("POST", self.base + "/api/restore?name=finances-nope.json")
        self.assertEqual(st, 404)

    # ---- investments module ------------------------------------------------

    def test_invest_page_serves_html(self):
        st, body, ctype = _raw("GET", self.base + "/invest")
        self.assertEqual(st, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Investments", body)

    def test_invest_accounts_lists_created_accounts(self):
        st, body = _req("GET", self.base + "/api/invest/accounts")
        self.assertEqual(st, 200)
        self.assertIsInstance(body, list)
        self.assertTrue(any(a["id"] == "kite-1" for a in body))  # created in setUpClass

    def test_invest_manual_then_summary_reflects_total(self):
        st, body = _req("POST", self.base + "/api/invest/manual", {
            "account": "kite-1",
            "rows": [{"symbol": "TESTCO", "quantity": 10, "avg_price": 100, "last_price": 110}],
        })
        self.assertEqual(st, 200)
        st, summary = _req("GET", self.base + "/api/invest/summary")
        self.assertEqual(st, 200)
        self.assertIn("total", summary)
        self.assertGreater(summary["total"], 0)
        self.assertIn("kite-1", summary["per_account"])
        self.assertEqual(summary["per_account"]["kite-1"]["value"], 1100)  # 10 * 110
        self.assertEqual(summary["per_asset_class"]["stocks"], summary["per_account"]["kite-1"]["value"])

    def test_invest_manual_unknown_account_400(self):
        st, body = _req("POST", self.base + "/api/invest/manual", {
            "account": "not-a-real-account",
            "rows": [{"symbol": "X", "quantity": 1, "avg_price": 1, "last_price": 1}],
        })
        self.assertEqual(st, 400)
        self.assertIn("error", body)

    def test_invest_holdings_endpoint_returns_stored_rows(self):
        # JOB-5 fix 2: the manual-holdings editor pre-fills existing rows via this
        # GET endpoint before add/edit/remove + POST /api/invest/manual.
        _req("POST", self.base + "/api/invest/manual", {
            "account": "kite-1",
            "rows": [{"symbol": "HOLDCO", "quantity": 3, "avg_price": 100, "last_price": 130}],
        })
        st, body = _req("GET", self.base + "/api/invest/holdings/kite-1")
        self.assertEqual(st, 200)
        self.assertEqual(body["account"], "kite-1")
        self.assertEqual(body["source"], "manual")
        syms = {r["symbol"] for r in body["rows"]}
        self.assertIn("HOLDCO", syms)

    def test_invest_holdings_endpoint_empty_for_unknown_account(self):
        st, body = _req("GET", self.base + "/api/invest/holdings/no-such-acct")
        self.assertEqual(st, 200)     # not an error — just an empty snapshot
        self.assertEqual(body["rows"], [])

    def test_invest_page_escapes_dynamic_values(self):
        # JOB-5 fix 1 (stored-XSS guard): invest.html must define the esc() helper
        # and apply it at the dynamic interpolation sites (account labels, IPO/company
        # names, symbols, owners, filenames). Rendering is client-side, so this
        # asserts the escaping is present in the served source, mirroring app.js.
        st, body, _ = _raw("GET", self.base + "/invest")
        self.assertEqual(st, 200)
        text = body.decode("utf-8")
        self.assertIn("const esc =", text)                 # helper exists
        for used in ("esc(i.name)", "esc(s.symbol)", "esc(a.label)",
                     "esc(a.broker)", "esc(b.symbol)", "esc(r.name)"):
            self.assertIn(used, text)                       # applied at each site
        # the previously-vulnerable raw interpolations must be gone
        self.assertNotIn("<td class=\"sym\">${i.name}", text)
        self.assertNotIn("<td class=\"sym\">${s.symbol}", text)

    def test_invest_page_has_manual_holdings_editor(self):
        # JOB-5 fix 2: the "Track manually" path now has a real add/edit/remove
        # holdings UI wired to POST /api/invest/manual.
        st, body, _ = _raw("GET", self.base + "/invest")
        self.assertEqual(st, 200)
        text = body.decode("utf-8")
        self.assertIn('id="holdings-modal"', text)
        self.assertIn("function openHoldings", text)
        self.assertIn("function saveHoldings", text)
        self.assertIn('post("/api/invest/manual"', text)
        self.assertIn("MANUAL_TYPES", text)

    def test_invest_unknown_route_404(self):
        st, body = _req("GET", self.base + "/api/invest/nope")
        self.assertEqual(st, 404)

    def test_invest_imports_lists_csv_and_xlsx_keys(self):
        st, body = _req("GET", self.base + "/api/invest/imports")
        self.assertEqual(st, 200)
        self.assertIn("csv", body)
        self.assertIn("xlsx", body)

    def test_invest_data_lands_under_tmp_data_dir(self):
        _req("POST", self.base + "/api/invest/manual", {
            "account": "kite-1",
            "rows": [{"symbol": "ISOCO", "quantity": 5, "avg_price": 50, "last_price": 55}],
        })
        invest_dir = os.path.join(self.tmp, "invest")
        self.assertTrue(os.path.isdir(invest_dir))
        self.assertTrue(os.path.isfile(os.path.join(invest_dir, "holdings.json")))

    # ---- owners + live values -> Portfolio bridge -------------------------
    # These use "wint-2" as a scratch account (untouched by the manual/
    # summary tests above, which live on kite-1) so owner + holdings state
    # set here can't race with other tests in this shared-server class.

    def test_invest_owner_set_happy_then_400_then_404(self):
        st, body = _req("POST", self.base + "/api/invest/accounts/wint-2/owner",
                        {"owner": "TestOwner"})
        self.assertEqual(st, 200)
        self.assertEqual(body["owner"], "TestOwner")

        st, body = _req("POST", self.base + "/api/invest/accounts/wint-2/owner",
                        {"owner": 123})
        self.assertEqual(st, 400)
        self.assertIn("error", body)

        st, body = _req("POST", self.base + "/api/invest/accounts/not-a-real-account/owner",
                        {"owner": "X"})
        self.assertEqual(st, 404)
        self.assertIn("error", body)

    def test_invest_owner_set_appends_new_name_to_finances_persons(self):
        _req("POST", self.base + "/api/invest/accounts/wint-2/owner",
             {"owner": "BrandNewInvestPerson"})
        st, data = _req("GET", self.base + "/api/data")
        self.assertEqual(st, 200)
        self.assertIn("BrandNewInvestPerson", data["settings"]["persons"])

    def test_invest_persons_union_endpoint(self):
        _req("POST", self.base + "/api/invest/accounts/wint-2/owner",
             {"owner": "UnionEndpointPerson"})
        st, body = _req("GET", self.base + "/api/invest/persons")
        self.assertEqual(st, 200)
        self.assertIn("UnionEndpointPerson", body["persons"])

    def test_get_data_includes_live_sync_row_for_owned_account_with_holdings(self):
        _req("POST", self.base + "/api/invest/accounts/wint-2/owner", {"owner": "LiveRowOwner"})
        _req("POST", self.base + "/api/invest/manual", {
            "account": "wint-2",
            "rows": [{"symbol": "BOND1", "quantity": 1, "avg_price": 1000, "last_price": 1000}],
        })
        st, data = _req("GET", self.base + "/api/data")
        self.assertEqual(st, 200)
        live = [r for r in data["portfolio"] if r.get("id") == "live-wint-2"]
        self.assertEqual(len(live), 1)
        self.assertTrue(live[0]["liveSync"])
        self.assertEqual(live[0]["owner"], "LiveRowOwner")
        self.assertEqual(live[0]["subCategory"], "Debt")
        self.assertEqual(live[0]["invested"], 1000.0)
        self.assertEqual(live[0]["currentValue"], 1000.0)

    def test_put_with_tampered_live_row_is_overwritten_by_authoritative_values(self):
        _req("POST", self.base + "/api/invest/accounts/wint-2/owner", {"owner": "LiveRowOwner2"})
        _req("POST", self.base + "/api/invest/manual", {
            "account": "wint-2",
            "rows": [{"symbol": "BOND2", "quantity": 1, "avg_price": 2000, "last_price": 2500}],
        })
        st, data = _req("GET", self.base + "/api/data")
        self.assertEqual(st, 200)
        tampered_portfolio = [r for r in data["portfolio"] if r.get("id") != "live-wint-2"]
        tampered_portfolio.append({
            "id": "live-wint-2", "liveSync": True, "investAccountId": "wint-2",
            "category": "hacked", "subCategory": "hacked", "owner": "LiveRowOwner2",
            "invested": 1, "currentValue": 999999, "lastSynced": "1970-01-01T00:00:00",
        })
        data["portfolio"] = tampered_portfolio
        st, _ = _req("PUT", self.base + "/api/data", data)
        self.assertEqual(st, 200)

        st, got = _req("GET", self.base + "/api/data")
        self.assertEqual(st, 200)
        live = [r for r in got["portfolio"] if r.get("id") == "live-wint-2"]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["currentValue"], 2500.0)  # authoritative, not the tampered 999999
        self.assertEqual(live[0]["invested"], 2000.0)


class DemoModeTests(unittest.TestCase):
    """--demo seeds the sample into an ISOLATED dir and never touches real data/."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fft-demo-")
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        env = dict(os.environ)
        env.pop("GEMINI_API_KEY", None)
        env["FF_NO_DOTENV"] = "1"  # a real .env must not re-inject keys
        # --demo with an explicit --data-dir keeps everything inside our temp sandbox
        cls.proc = subprocess.Popen(
            [sys.executable, SERVER, "--demo", "--port", str(cls.port),
             "--data-dir", cls.tmp, "--no-browser"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        for _ in range(50):
            try:
                with urllib.request.urlopen(cls.base + "/api/data", timeout=1):
                    break
            except Exception:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_demo_seeds_sample_dataset(self):
        st, got = _req("GET", self.base + "/api/data")
        self.assertEqual(st, 200)
        self.assertTrue(got["income"]["persons"], "demo should be seeded with sample earners")
        self.assertEqual(got["settings"]["appName"], "Sharma Family Finance")

    def test_demo_writes_stay_in_sandbox_not_real_data(self):
        # the seeded finances.json + a backup must land in the temp sandbox
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "finances.json")))
        _req("POST", self.base + "/api/backup")
        baks = os.listdir(os.path.join(self.tmp, "backups"))
        self.assertTrue(baks, "backup written into sandbox")
        # and the real project data/finances.json was never created by this test run
        # (we only assert the sandbox is self-contained — real data/ is a separate path)
        self.assertNotEqual(os.path.abspath(self.tmp), os.path.join(ROOT, "data"))

    def test_demo_seeds_invest_holdings(self):
        st, summary = _req("GET", self.base + "/api/invest/summary")
        self.assertEqual(st, 200)
        self.assertGreater(summary["total"], 0)
        self.assertIn("kite-1", summary["per_account"])
        self.assertIn("wint-1", summary["per_account"])

    def test_demo_ipo_close_date_is_relative_not_absurd(self):
        # JOB-5 fix 11: the demo IPO's close date is seeded ~2 weeks out from the
        # seed time, so it reads as a realistic "days left" instead of the old
        # fixed 2099-01-02 ("26461d left").
        st, ipos = _req("GET", self.base + "/api/invest/ipos")
        self.assertEqual(st, 200)
        demo = next((i for i in ipos if i["name"] == "Demo Foods"), None)
        self.assertIsNotNone(demo, "demo IPO 'Demo Foods' should be tracked")
        self.assertLessEqual(demo["days_to_close"], 21)   # weeks, not centuries
        self.assertGreaterEqual(demo["days_to_close"], 0)

    def test_demo_data_includes_live_portfolio_rows(self):
        """The live->Portfolio bridge must be visibly demoable: demo invest
        accounts are seeded with owners matching the demo finances persons."""
        st, got = _req("GET", self.base + "/api/data")
        self.assertEqual(st, 200)
        live = [r for r in got["portfolio"] if r.get("liveSync")]
        self.assertTrue(live, "demo Portfolio should show at least one live-synced invest account")
        owners = {r["owner"] for r in live}
        self.assertTrue(owners & set(got["settings"]["persons"]),
                        "live row owners should already be known demo persons")

    def test_demo_invest_files_stay_in_sandbox(self):
        invest_dir = os.path.join(self.tmp, "invest")
        self.assertTrue(os.path.isfile(os.path.join(invest_dir, "holdings.json")))
        # same isolation guarantee as test_demo_writes_stay_in_sandbox_not_real_data:
        # we only assert the sandbox path is distinct from the real data/ path —
        # we never inspect or touch the real data/ dir itself.
        real_invest_dir = os.path.join(ROOT, "data", "invest")
        self.assertNotEqual(os.path.abspath(invest_dir), os.path.abspath(real_invest_dir))


class BlankSlateInvestTests(unittest.TestCase):
    """A fresh data dir starts with ZERO accounts, shows the onboarding UI, and
    isolates imports/ to the data dir (D1 / D4 / D8)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fft-blank-")
        # a marker file in THIS data dir's imports/ proves isolation below
        os.makedirs(os.path.join(cls.tmp, "imports"), exist_ok=True)
        with open(os.path.join(cls.tmp, "imports", "only-here.csv"), "w", encoding="utf-8") as f:
            f.write("Symbol,Qty\nX,1\n")
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        env = dict(os.environ)
        env.pop("GEMINI_API_KEY", None)
        env["FF_NO_DOTENV"] = "1"
        cls.proc = subprocess.Popen(
            [sys.executable, SERVER, "--port", str(cls.port),
             "--data-dir", cls.tmp, "--no-browser"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        for _ in range(50):
            try:
                with urllib.request.urlopen(cls.base + "/api/data", timeout=1):
                    break
            except Exception:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_accounts_start_empty(self):
        st, body = _req("GET", self.base + "/api/invest/accounts")
        self.assertEqual(st, 200)
        self.assertEqual(body, [])  # no seeded example/personal accounts

    def test_persons_start_empty(self):
        st, body = _req("GET", self.base + "/api/invest/persons")
        self.assertEqual(st, 200)
        self.assertEqual(body["persons"], [])

    def test_summary_is_zero(self):
        st, body = _req("GET", self.base + "/api/invest/summary")
        self.assertEqual(st, 200)
        self.assertEqual(body["total"], 0.0)
        self.assertEqual(body["per_account"], {})

    def test_tokens_empty_without_accounts(self):
        st, body = _req("GET", self.base + "/api/invest/tokens")
        self.assertEqual(st, 200)
        self.assertEqual(body, {})

    def test_invest_page_has_onboarding_hero(self):
        st, body, ctype = _raw("GET", self.base + "/invest")
        self.assertEqual(st, 200)
        self.assertIn(b'id="onboarding"', body)
        self.assertIn(b"Set up your investments", body)
        self.assertIn(b"Connect a broker", body)

    def test_imports_isolated_to_data_dir(self):
        st, body = _req("GET", self.base + "/api/invest/imports")
        self.assertEqual(st, 200)
        # sees ONLY the file in <data-dir>/imports — never the real repo imports/
        self.assertEqual(body["csv"], ["only-here.csv"])


class AccountCrudTests(unittest.TestCase):
    """Create / edit / delete accounts over HTTP, including the holdings-block
    on delete and the ?force=1 override (D2)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fft-crud-")
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        env = dict(os.environ)
        env.pop("GEMINI_API_KEY", None)
        env["FF_NO_DOTENV"] = "1"
        cls.proc = subprocess.Popen(
            [sys.executable, SERVER, "--port", str(cls.port),
             "--data-dir", cls.tmp, "--no-browser"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        for _ in range(50):
            try:
                with urllib.request.urlopen(cls.base + "/api/data", timeout=1):
                    break
            except Exception:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_crud_lifecycle(self):
        # create
        st, acct = _req("POST", self.base + "/api/invest/accounts",
                        {"id": "kite-1", "broker_type": "kite", "label": "My Kite", "owner": "Jordan"})
        self.assertEqual(st, 200)
        self.assertEqual(acct["broker_type"], "kite")
        self.assertEqual(acct["asset_class"], "stocks")
        # appears in the registry
        st, accts = _req("GET", self.base + "/api/invest/accounts")
        self.assertTrue(any(a["id"] == "kite-1" for a in accts))
        # a new owner propagates to finances persons + the persons union
        st, data = _req("GET", self.base + "/api/data")
        self.assertIn("Jordan", data["settings"]["persons"])
        st, p = _req("GET", self.base + "/api/invest/persons")
        self.assertIn("Jordan", p["persons"])
        # edit label + owner
        st, upd = _req("POST", self.base + "/api/invest/accounts/kite-1",
                       {"label": "Renamed", "owner": "Sam"})
        self.assertEqual(st, 200)
        self.assertEqual(upd["label"], "Renamed")
        self.assertEqual(upd["owner"], "Sam")
        # duplicate id / bad broker type / bad id all 400
        st, _ = _req("POST", self.base + "/api/invest/accounts", {"id": "kite-1", "broker_type": "kite"})
        self.assertEqual(st, 400)
        st, _ = _req("POST", self.base + "/api/invest/accounts", {"id": "x-1", "broker_type": "nope"})
        self.assertEqual(st, 400)
        st, _ = _req("POST", self.base + "/api/invest/accounts", {"id": "Bad Id", "broker_type": "kite"})
        self.assertEqual(st, 400)
        # holdings block delete unless forced
        _req("POST", self.base + "/api/invest/manual",
             {"account": "kite-1", "rows": [{"symbol": "A", "quantity": 1, "avg_price": 10, "last_price": 10}]})
        st, e = _req("DELETE", self.base + "/api/invest/accounts/kite-1")
        self.assertEqual(st, 400)
        self.assertIn("holding", e["error"].lower())
        # force delete removes the account (and its holdings)
        st, r = _req("DELETE", self.base + "/api/invest/accounts/kite-1?force=1")
        self.assertEqual(st, 200)
        st, accts = _req("GET", self.base + "/api/invest/accounts")
        self.assertFalse(any(a["id"] == "kite-1" for a in accts))

    def test_delete_unknown_account_400(self):
        st, e = _req("DELETE", self.base + "/api/invest/accounts/ghost-1")
        self.assertEqual(st, 400)
        self.assertIn("error", e)

    def test_overlong_account_id_rejected(self):
        # JOB-5 fix: an id over 40 chars is refused server-side with a clear message
        # (previously accepted, blowing out the Accounts-table width). A 40-char id
        # is still fine.
        long_id = "m" + "a" * 40  # 41 chars
        st, e = _req("POST", self.base + "/api/invest/accounts",
                     {"id": long_id, "broker_type": "manual"})
        self.assertEqual(st, 400)
        self.assertIn("40", e["error"])
        st, ok = _req("POST", self.base + "/api/invest/accounts",
                      {"id": "m" + "a" * 39, "broker_type": "manual"})  # exactly 40
        self.assertEqual(st, 200)


class InvestTokensHttpTests(unittest.TestCase):
    """GET /api/invest/tokens over HTTP — unconfigured / configured-but-stale /
    Upstox long-lived Analytics Token branches (findings/d-demo-tests.md: this
    route was never hit over HTTP before). All env values below are obviously
    fake and only ever compared, never sent anywhere."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fft-tokens-")
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        env = dict(os.environ)
        env.pop("GEMINI_API_KEY", None)
        env["FF_NO_DOTENV"] = "1"
        env["KITE_TOK_CONF_API_KEY"] = "fake-kite-key"
        env["UPSTOX_TOK_LONGLIVED_API_KEY"] = "fake-upstox-key"
        env["UPSTOX_TOK_LONGLIVED_ANALYTICS_TOKEN"] = "fake-analytics-token"
        cls.proc = subprocess.Popen(
            [sys.executable, SERVER, "--port", str(cls.port),
             "--data-dir", cls.tmp, "--no-browser"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        for _ in range(50):
            try:
                with urllib.request.urlopen(cls.base + "/api/data", timeout=1):
                    break
            except Exception:
                time.sleep(0.2)
        for spec in ({"id": "kite-tok-unconf", "broker_type": "kite"},
                     {"id": "kite-tok-conf", "broker_type": "kite"},
                     {"id": "upstox-tok-longlived", "broker_type": "upstox"}):
            _req("POST", cls.base + "/api/invest/accounts", spec)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_tokens_unconfigured_account(self):
        st, body = _req("GET", self.base + "/api/invest/tokens")
        self.assertEqual(st, 200)
        self.assertEqual(body["kite-tok-unconf"],
                         {"kind": "kite", "configured": False,
                          "token_fresh": False, "long_lived": False})

    def test_tokens_configured_without_fresh_token(self):
        st, body = _req("GET", self.base + "/api/invest/tokens")
        self.assertEqual(st, 200)
        self.assertEqual(body["kite-tok-conf"],
                         {"kind": "kite", "configured": True,
                          "token_fresh": False, "long_lived": False})

    def test_tokens_upstox_long_lived_analytics_token_counts_as_fresh(self):
        st, body = _req("GET", self.base + "/api/invest/tokens")
        self.assertEqual(st, 200)
        self.assertEqual(body["upstox-tok-longlived"],
                         {"kind": "upstox", "configured": True,
                          "token_fresh": True, "long_lived": True})


class OAuthAndSyncTests(unittest.TestCase):
    """OAuth login/callback (/auth/<kite|upstox>/<account>/<login|callback>)
    and the broker-pull sync endpoint (POST /api/invest/sync/<account>),
    exercised over real HTTP against an IN-PROCESS server (see
    _boot_inprocess_server above) so investlib.brokers' network-calling
    functions can be mocked — findings/d-demo-tests.md flagged both routes as
    having zero test coverage at any layer. This class must never make a real
    broker/NSE network call; every "success" path replaces the actual
    network-touching brokers function with a mock."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fft-oauth-")
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.srv, cls.httpd, cls.thread = _boot_inprocess_server(cls.tmp, cls.port)
        for _ in range(50):
            try:
                with urllib.request.urlopen(cls.base + "/api/data", timeout=1):
                    break
            except Exception:
                time.sleep(0.1)
        from investlib import brokers
        cls.brokers = brokers
        # One fake account per scenario so tests can never collide with each other.
        for spec in ({"id": "kite-oauth-a", "broker_type": "kite"},
                     {"id": "kite-oauth-b", "broker_type": "kite"},
                     {"id": "upstox-oauth-a", "broker_type": "upstox"},
                     {"id": "upstox-oauth-noenv", "broker_type": "upstox"},
                     {"id": "kite-sync-a", "broker_type": "kite"},
                     {"id": "kite-sync-extras", "broker_type": "kite"}):
            _req("POST", cls.base + "/api/invest/accounts", spec)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.srv.DATA_DIR = None  # restore the module global server.py sets in main()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- GET /auth/<kind>/<account>/login ---------------------------------

    def test_login_success_redirects_to_mocked_broker_url(self):
        with mock.patch.object(self.brokers, "login_url",
                               return_value="https://kite.example.invalid/connect/login?fake=1"):
            st, loc, _ = _req_no_redirect(self.base + "/auth/kite/kite-oauth-a/login")
        self.assertEqual(st, 302)
        self.assertEqual(loc, "https://kite.example.invalid/connect/login?fake=1")

    def test_login_unknown_account_is_a_guided_error(self):
        st, loc, body = _req_no_redirect(self.base + "/auth/kite/kite-oauth-ghost/login")
        self.assertIsNone(loc)
        # invest_api._auth has no ValueError->400 narrowing like the POST routes do
        # (falls into handle_get's generic except -> 500) — see report, not fixed here.
        self.assertEqual(st, 500)
        self.assertIn("kite-oauth-ghost", body["error"])

    def test_login_missing_env_keys_is_a_guided_error(self):
        # Upstox only needs _env() to build the login URL (no optional-extras
        # import in the way), so this cleanly isolates the "missing .env keys"
        # message from the separate "missing kiteconnect/pyotp extras" message
        # covered by the sync test below.
        st, loc, body = _req_no_redirect(self.base + "/auth/upstox/upstox-oauth-noenv/login")
        self.assertIsNone(loc)
        self.assertEqual(st, 500)
        self.assertIn("UPSTOX_OAUTH_NOENV_API_KEY", body["error"])
        self.assertIn(".env", body["error"])

    def test_auth_malformed_path_404(self):
        st, body = _req("GET", self.base + "/auth/kite/onlyone")
        self.assertEqual(st, 404)
        self.assertIn("bad auth path", body["error"])

    def test_auth_unknown_step_404(self):
        st, body = _req("GET", self.base + "/auth/kite/kite-oauth-a/frobnicate")
        self.assertEqual(st, 404)

    # ---- GET /auth/<kind>/<account>/callback -------------------------------

    def test_callback_success_kite_saves_token_and_redirects(self):
        def fake_exchange(account_id, request_token):
            self.assertEqual(request_token, "faketoken123")
            self.brokers.save_token(account_id, "fake-access-token")

        with mock.patch.object(self.brokers, "kite_exchange", side_effect=fake_exchange) as m_ex, \
                mock.patch.object(self.brokers, "sync", return_value={}) as m_sync:
            st, loc, _ = _req_no_redirect(
                self.base + "/auth/kite/kite-oauth-b/callback?request_token=faketoken123")
        self.assertEqual(st, 302)
        self.assertEqual(loc, "/invest")
        m_ex.assert_called_once_with("kite-oauth-b", "faketoken123")
        m_sync.assert_called_once_with("kite-oauth-b")
        # the token really landed in the store, not just a mock call record
        st, body = _req("GET", self.base + "/api/invest/tokens")
        self.assertTrue(body["kite-oauth-b"]["token_fresh"])

    def test_callback_success_upstox_saves_token_and_redirects(self):
        def fake_exchange(account_id, code):
            self.assertEqual(code, "fakecode456")
            self.brokers.save_token(account_id, "fake-access-token")

        with mock.patch.object(self.brokers, "upstox_exchange", side_effect=fake_exchange) as m_ex, \
                mock.patch.object(self.brokers, "sync", return_value={}) as m_sync:
            st, loc, _ = _req_no_redirect(
                self.base + "/auth/upstox/upstox-oauth-a/callback?code=fakecode456")
        self.assertEqual(st, 302)
        self.assertEqual(loc, "/invest")
        m_ex.assert_called_once_with("upstox-oauth-a", "fakecode456")
        m_sync.assert_called_once_with("upstox-oauth-a")

    def test_callback_unknown_broker_kind_404(self):
        st, loc, body = _req_no_redirect(
            self.base + "/auth/robinhood/kite-oauth-a/callback?code=x")
        self.assertIsNone(loc)
        self.assertEqual(st, 404)
        self.assertIn("robinhood", body["error"])

    def test_callback_missing_request_token_is_a_guided_error(self):
        # kite_exchange is deliberately NOT mocked here: query["request_token"]
        # raises KeyError before brokers.kite_exchange is ever called, so this
        # is real behaviour, not a mock artifact.
        st, loc, body = _req_no_redirect(self.base + "/auth/kite/kite-oauth-a/callback")
        self.assertIsNone(loc)
        self.assertEqual(st, 500)
        self.assertIn("error", body)

    def test_callback_missing_code_is_a_guided_error(self):
        st, loc, body = _req_no_redirect(self.base + "/auth/upstox/upstox-oauth-a/callback")
        self.assertIsNone(loc)
        self.assertEqual(st, 500)
        self.assertIn("error", body)

    def test_callback_bad_request_token_surfaces_broker_rejection(self):
        with mock.patch.object(self.brokers, "kite_exchange",
                               side_effect=ValueError("invalid request_token")):
            st, loc, body = _req_no_redirect(
                self.base + "/auth/kite/kite-oauth-a/callback?request_token=bad")
        self.assertIsNone(loc)
        self.assertEqual(st, 500)
        self.assertIn("invalid request_token", body["error"])

    def test_callback_unknown_account_surfaces_sync_error_after_exchange(self):
        # kite_exchange itself doesn't check the account registry; the
        # follow-up brokers.sync(account_id) call does — sync is real
        # (unmocked) here on purpose to prove that path.
        with mock.patch.object(self.brokers, "kite_exchange", return_value=None):
            st, loc, body = _req_no_redirect(
                self.base + "/auth/kite/kite-oauth-ghost/callback?request_token=x")
        self.assertIsNone(loc)
        self.assertEqual(st, 500)
        self.assertIn("kite-oauth-ghost", body["error"])

    # ---- POST /api/invest/sync/<account> -----------------------------------

    def test_sync_success_mocked_broker_layer(self):
        with mock.patch.object(self.brokers, "sync", return_value={"kite-sync-a": 3}) as m:
            st, body = _req("POST", self.base + "/api/invest/sync/kite-sync-a")
        self.assertEqual(st, 200)
        self.assertEqual(body, {"kite-sync-a": 3})
        m.assert_called_once_with("kite-sync-a")

    def test_sync_unknown_account_400(self):
        st, body = _req("POST", self.base + "/api/invest/sync/ghost-no-such-account")
        self.assertEqual(st, 400)
        self.assertIn("ghost-no-such-account", body["error"])

    def test_sync_missing_extras_degrades_with_guided_error(self):
        # A fresh token must exist first, or kite_sync fails on the (unrelated)
        # "no token" check before ever reaching the kiteconnect import. Forcing
        # kiteconnect "absent" via sys.modules keeps this deterministic
        # regardless of whether the host actually has it installed (mirrors
        # tests/test_investlib.py::TestMissingExtras).
        self.brokers.save_token("kite-sync-extras", "fake-access-token")
        with mock.patch.dict(sys.modules, {"kiteconnect": None}):
            st, body = _req("POST", self.base + "/api/invest/sync/kite-sync-extras")
        self.assertEqual(st, 500)
        self.assertIn("requirements-invest.txt", body["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

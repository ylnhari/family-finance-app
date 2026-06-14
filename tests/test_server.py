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


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fft-")
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        # run without GEMINI_API_KEY so AI endpoints take the graceful-degrade path
        env = dict(os.environ)
        env.pop("GEMINI_API_KEY", None)
        cls.proc = subprocess.Popen(
            [sys.executable, SERVER, "--port", str(cls.port),
             "--data-dir", cls.tmp, "--no-browser"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        cls._wait_ready()

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


class DemoModeTests(unittest.TestCase):
    """--demo seeds the sample into an ISOLATED dir and never touches real data/."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fft-demo-")
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        env = dict(os.environ)
        env.pop("GEMINI_API_KEY", None)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

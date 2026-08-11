"""Tests for the /docs/*.md static route (serves docs/BROKER-SETUP.md and
docs/NOTIFICATIONS.md so the /invest onboarding panel's links work offline,
without a GitHub round-trip). Zero dependencies (stdlib unittest).

Run from the project root:
    python -m unittest discover -s tests -p "test_*.py" -v

Boots a real server on a throwaway port + temp data dir, hits it over HTTP —
same pattern as test_server.py. Does NOT touch tests/test_server.py.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server.py")
DOCS_DIR = os.path.join(ROOT, "docs")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _raw(method, url):
    r = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


class DocsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fft-docs-")
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        env = dict(os.environ)
        env["FF_NO_DOTENV"] = "1"  # a real .env must not leak into this boot
        cls.proc = subprocess.Popen(
            [sys.executable, SERVER, "--port", str(cls.port),
             "--data-dir", cls.tmp, "--no-browser"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        cls._wait_ready()

    @classmethod
    def _wait_ready(cls):
        for _ in range(50):
            try:
                with urllib.request.urlopen(cls.base + "/api/ping", timeout=1):
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

    # ---- happy path --------------------------------------------------

    def test_broker_setup_doc_served(self):
        st, body, ctype = _raw("GET", self.base + "/docs/BROKER-SETUP.md")
        self.assertEqual(st, 200)
        # JOB-5 fix 8: docs are now rendered to HTML server-side, not served raw
        self.assertIn("text/html", ctype)
        text = body.decode("utf-8")
        self.assertIn("<!doctype html>", text.lower())
        self.assertRegex(text, r"<h[1-3]>")           # markdown headings became tags
        self.assertIn("developers.kite.trade", text)
        self.assertIn("account.upstox.com/developer/apps", text)
        # env var naming convention documented, matching investlib/brokers.py _env()
        self.assertIn("_API_KEY", text)
        self.assertIn("COIN_SOURCE_ACCOUNT", text)
        # raw markdown heading markers must NOT survive as literal text lines
        self.assertNotRegex(text, r"(?m)^#{1,6} ")

    def test_notifications_doc_served(self):
        st, body, ctype = _raw("GET", self.base + "/docs/NOTIFICATIONS.md")
        self.assertEqual(st, 200)
        self.assertIn("text/html", ctype)
        text = body.decode("utf-8")
        self.assertIn("<!doctype html>", text.lower())
        self.assertIn("ntfy.sh", text)
        self.assertIn("INVESTMENTS_NTFY_TOPIC", text)
        self.assertIn("daily_brief.py", text)
        self.assertIn("refresh_tokens.py", text)

    def test_markdown_renderer_is_html_safe(self):
        # The renderer escapes HTML BEFORE adding its own tags, so a doc that
        # contained raw markup could never inject live nodes. Exercised directly
        # (no server needed) against server.render_markdown.
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import server as srv
        html = srv.render_markdown(
            "# Title\n\nSome <script>alert(1)</script> & \"quotes\".\n\n"
            "- one\n- two\n\n[Kite](https://developers.kite.trade/) and `CODE`\n\n"
            "```\nraw <b>fenced</b>\n```\n")
        self.assertIn("<h1>Title</h1>", html)
        self.assertNotIn("<script>alert(1)</script>", html)   # escaped, inert
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("<li>one</li>", html)
        self.assertIn('<a href="https://developers.kite.trade/"', html)
        self.assertIn("<code>CODE</code>", html)
        self.assertIn("&lt;b&gt;fenced&lt;/b&gt;", html)      # fenced code stays literal

    def test_markdown_table_renders_to_html_table(self):
        # p4v3 finding #2: GFM pipe tables ("| a | b |" + "|---|---|" separator)
        # must become a real <table>, not literal pipe-delimited <p> tags.
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import server as srv
        html = srv.render_markdown(
            "# Doc\n\n"
            "| Suffix | Required for | Example |\n"
            "|---|---|---|\n"
            "| `_API_KEY` | Kite, Upstox | `KITE_1_API_KEY` |\n"
            "| `_USER_ID` | Kite headless refresh only | `KITE_1_USER_ID` |\n\n"
            "Trailing paragraph.\n"
        )
        self.assertIn("<table>", html)
        self.assertIn("<thead><tr><th>Suffix</th><th>Required for</th><th>Example</th></tr></thead>", html)
        self.assertIn("<tbody>", html)
        self.assertIn("<td><code>_API_KEY</code></td>", html)
        self.assertIn("<td><code>_USER_ID</code></td>", html)
        self.assertIn("</table>", html)
        # the paragraph right after the table must still render normally —
        # table-row consumption shouldn't swallow unrelated following lines
        self.assertIn("<p>Trailing paragraph.</p>", html)
        # no leftover literal pipe-delimited lines
        self.assertNotRegex(html, r"<p>\|.*\|</p>")

    def test_soft_wrapped_list_item_stays_one_li(self):
        # A bullet whose text is hard-wrapped across source lines must render as a
        # SINGLE <li>. It used to close the <ul> and emit the continuation as an
        # un-indented <p> under the bullet, which read as broken layout in the
        # in-app docs viewer.
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import server as srv
        html = srv.render_markdown(
            "- Know which port your app serves on. Default is **8765**; you may\n"
            "  have set a different one via `--port`.\n"
            "- Second bullet.\n"
            "\n"
            "A real paragraph after the list.\n"
        )
        self.assertIn("have set a different one", html)
        # the continuation belongs inside the first <li>, not in its own <p>
        self.assertNotRegex(html, r"<p>\s*have set a different")
        self.assertEqual(html.count("<li>"), 2)
        self.assertEqual(html.count("<ul>"), 1)  # list not split in two
        first_li = html.split("<li>")[1].split("</li>")[0]
        self.assertIn("Default is <strong>8765</strong>", first_li)
        self.assertIn("<code>--port</code>", first_li)
        # a genuine paragraph after a blank line still closes the list normally
        self.assertIn("</ul>", html)
        self.assertIn("<p>A real paragraph after the list.</p>", html)

    def test_markdown_table_alignment_colons(self):
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import server as srv
        html = srv.render_markdown(
            "| Left | Center | Right |\n|:---|:---:|---:|\n| a | b | c |\n"
        )
        self.assertIn('<th style="text-align:left">Left</th>', html)
        self.assertIn('<th style="text-align:center">Center</th>', html)
        self.assertIn('<th style="text-align:right">Right</th>', html)

    def test_markdown_table_cell_html_injection_is_escaped(self):
        # Same XSS guard as test_markdown_renderer_is_html_safe, but specifically
        # for content inside a table cell (a distinct code path from paragraphs).
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import server as srv
        html = srv.render_markdown(
            "| Col |\n|---|\n"
            "| <script>alert(1)</script> |\n"
            "| <img src=x onerror=alert(2)> |\n"
        )
        self.assertIn("<table>", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x onerror=alert(2)>", html)
        self.assertIn("<td>&lt;script&gt;alert(1)&lt;/script&gt;</td>", html)
        self.assertIn("&lt;img src=x onerror=alert(2)&gt;", html)

    def test_broker_setup_doc_table_renders_in_app(self):
        # End-to-end version of the p4v3 finding: hit the real served route and
        # confirm the .env suffix-mapping table (Step 4) is a real <table>.
        st, body, _ = _raw("GET", self.base + "/docs/BROKER-SETUP.md")
        self.assertEqual(st, 200)
        text = body.decode("utf-8")
        self.assertIn("<table>", text)
        self.assertIn("<th>Suffix</th>", text)
        self.assertIn("<td><code>_API_KEY</code></td>", text)
        self.assertNotRegex(text, r"<p>\|.*\|</p>")

    def test_docs_route_matches_actual_files_on_disk(self):
        # The route serves whatever's really in docs/ — cross-check both
        # tracked files exist so this test can't silently drift from disk.
        for name in ("BROKER-SETUP.md", "NOTIFICATIONS.md"):
            self.assertTrue(os.path.isfile(os.path.join(DOCS_DIR, name)))

    def test_invest_html_links_to_local_docs_path(self):
        # D4/JOB-3: onboarding panel should link the local offline path first
        # (GitHub link may remain as a secondary link).
        st, body, _ = _raw("GET", self.base + "/invest")
        self.assertEqual(st, 200)
        self.assertIn(b'href="/docs/BROKER-SETUP.md"', body)

    # ---- error paths ---------------------------------------------------

    def test_missing_doc_404s(self):
        st, body, _ = _raw("GET", self.base + "/docs/NOPE.md")
        self.assertEqual(st, 404)

    def test_non_md_extension_rejected(self):
        # extension allowlist: only .md is servable from docs/, even if the
        # underlying file happens to exist with a different name.
        st, body, _ = _raw("GET", self.base + "/docs/BROKER-SETUP")
        self.assertEqual(st, 404)

    def test_path_traversal_on_docs_get_is_blocked(self):
        # Reaching a real, non-secret tracked file (CHANGELOG.md) one level
        # above docs/ must still be refused — the guard is about staying
        # inside docs/, not about which file is targeted.
        st, body, _ = _raw("GET", self.base + "/docs/..%2fCHANGELOG.md")
        self.assertIn(st, (400, 403, 404))
        self.assertNotIn(b"Keep a Changelog", body)

    def test_path_traversal_with_literal_dots_blocked(self):
        # Same guard, unencoded form — targets a different real, non-secret
        # tracked file (CONTRIBUTING.md) so this doesn't just re-test the
        # previous case with different encoding of the same target.
        st, body, _ = _raw("GET", self.base + "/docs/../CONTRIBUTING.md")
        self.assertIn(st, (400, 403, 404))
        self.assertNotIn(b"Golden rules", body)


if __name__ == "__main__":
    unittest.main()

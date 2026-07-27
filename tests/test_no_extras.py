"""Regression guard: every module must import cleanly with the optional
live-sync extras (requests, kiteconnect, pyotp, upstox_client) forced
absent -- even on a machine where they ARE pip-installed (e.g. this repo's
own investments `.venv`, which has kiteconnect/upstox-python-sdk/pyotp/
requests installed for live broker sync).

Why this exists: CLAUDE.md promises the app "must run with no installs at
all" -- requirements-invest.txt is optional, lazily-imported extras only.
That promise broke once: `investlib/ipo_fetch.py` had
`def _session() -> requests.Session:` -- a module-level function annotation,
which CPython evaluates at *import time* unless the module opts into
`from __future__ import annotations` (PEP 563). With no extras installed,
`requests` is the `_MissingRequests` shim (see brokers.py/ipo_fetch.py),
whose `__getattr__` raises RuntimeError -- so evaluating `requests.Session`
at import time raised, and `investlib.ipo_fetch` (and therefore server.py,
which imports it transitively via invest_api) failed to import at all. The
app would not even start with a bare `pip install`.

Every developer machine that ran the suite locally happened to have
`requests` installed (from the investments `.venv` or a global env), so this
was invisible until GitHub CI -- which installs no extras -- caught it.

This test closes that blind spot permanently, on ANY machine: it forces the
optional deps absent via the well-known `sys.modules[name] = None` trick
(CPython's import system checks `sys.modules` first and raises `ImportError`
immediately if the cached entry is None, before ever touching whatever's
actually pip-installed) and then imports every app module fresh, in a
*subprocess* -- so a broken import can't corrupt this test process's module
cache for every other test that runs afterward, and so the result is
independent of whether this interpreter happens to have the extras
installed.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Every module that must be importable with zero installs, per CLAUDE.md's
# "no cloud, no accounts, no external deps" promise -- the full set of
# modules the orchestrator's own worker prompt names, plus the rest of
# investlib/ for completeness. (tools/e2e/runner.py is intentionally
# excluded: it's an opt-in dev tool that requires `websockets` and
# sys.exits with a clear message if it's missing -- it's not part of the
# app and was never supposed to run with zero installs.)
MODULES = [
    "config",
    "server",
    "invest_api",
    "invest_cli",
    "daily_brief",
    "refresh_tokens",
    "investlib.analysis",
    "investlib.bridge",
    "investlib.brokers",
    "investlib.ipo",
    "investlib.ipo_fetch",
    "investlib.ipo_history",
    "investlib.portfolio",
    "investlib.store",
    "investlib.wintwealth",
    "investlib.xlsx_lite",
]

# The full sanctioned-exception list from requirements-invest.txt (CLAUDE.md
# rule 1): kiteconnect, upstox-python-sdk (imported as upstox_client),
# requests, pyotp.
BLOCKED = ("requests", "kiteconnect", "pyotp", "upstox_client")

_RUNNER = """
import importlib
import sys

for _name in {blocked!r}:
    sys.modules[_name] = None  # CPython raises ImportError on any `import`
                                # of a name cached as None in sys.modules --
                                # true regardless of what's actually installed.

failures = []
for mod in {modules!r}:
    try:
        importlib.import_module(mod)
    except Exception as e:
        failures.append("%s: %r" % (mod, e))

if failures:
    sys.stderr.write(chr(10).join(failures) + chr(10))
    sys.exit(1)
sys.exit(0)
"""


class NoExtrasImportTests(unittest.TestCase):
    def test_every_module_imports_with_extras_forced_absent(self):
        """Import every app module with requests/kiteconnect/pyotp/
        upstox_client forced absent, in a subprocess, so this is true on ANY
        machine -- including one where the extras ARE pip-installed. Fails
        loudly if anyone reintroduces a module-level (import-time) use of an
        optional dep, such as an unstringified function/variable type
        annotation, a default argument value, a class attribute, a
        decorator, or a module-level constant built from one of these libs.
        """
        script = _RUNNER.format(blocked=BLOCKED, modules=MODULES)
        env = dict(os.environ)
        env["FF_NO_DOTENV"] = "1"  # don't let a real .env change import-time behaviour
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(
            proc.returncode, 0,
            "One or more modules failed to import with the optional "
            "live-sync extras forced absent -- this is exactly the class "
            "of bug that broke CI on a bare `pip install` (no extras): a "
            "module-level / import-time use of requests, kiteconnect, "
            "pyotp, or upstox_client somewhere in the import chain below. "
            "See the module docstring for the cb77f24 story.\n\n" + proc.stderr)


if __name__ == "__main__":
    unittest.main()

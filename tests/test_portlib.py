"""The vendored port resolver.

This file is the reference test suite for portlib.py — it travels with the
helper into every repo, so the previous per-project implementations converge on
one tested behaviour instead of several slightly different ones.

Only the import block below may be adapted per repo. The assertions are the
contract: resolution order, the registry being optional, never reading
`next_available`, and never quietly moving to a different port.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# The ONLY block that may differ between copies: point it at wherever this repo
# keeps portlib.py. Everything below must stay identical, because these
# assertions are what keep the copies honest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import portlib  # noqa: E402

KEY = "demo-app"


def write_registry(root: Path, payload: dict) -> Path:
    p = root / "ports.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def full(port=8790, extra=None):
    reg = {"registry": {KEY: {"port": port, "status": "active"}},
           "next_available": 9999}
    if extra:
        reg["registry"].update(extra)
    return reg


class TestPrecedence(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.reg = write_registry(self.root, full(8790))

    def test_explicit_beats_everything(self):
        with mock.patch.dict(os.environ, {"DEMO_PORT": "7000"}):
            got = portlib.resolve_port(KEY, explicit=1234, env_var="DEMO_PORT",
                                       default=8888, registry=self.reg)
        self.assertEqual(got, 1234)

    def test_env_beats_registry_and_default(self):
        with mock.patch.dict(os.environ, {"DEMO_PORT": "7000"}):
            got = portlib.resolve_port(KEY, env_var="DEMO_PORT", default=8888,
                                       registry=self.reg)
        self.assertEqual(got, 7000)

    def test_registry_beats_default(self):
        got = portlib.resolve_port(KEY, default=8888, registry=self.reg)
        self.assertEqual(got, 8790)

    def test_default_used_when_registry_absent(self):
        """The open-source case: a clone with no ports.json anywhere above it."""
        with TemporaryDirectory() as clone:
            got = portlib.resolve_port(KEY, default=8888,
                                       start=Path(clone), registry=None)
        self.assertEqual(got, 8888)

    def test_default_used_when_entry_absent(self):
        reg = write_registry(self.root, {"registry": {"other": {"port": 1}},
                                         "next_available": 9999})
        self.assertEqual(portlib.resolve_port(KEY, default=8888, registry=reg), 8888)

    def test_empty_env_is_ignored_not_an_error(self):
        with mock.patch.dict(os.environ, {"DEMO_PORT": ""}):
            self.assertEqual(
                portlib.resolve_port(KEY, env_var="DEMO_PORT", registry=self.reg),
                8790)


class TestRefusesToGuess(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_never_reads_next_available(self):
        """The whole point: next_available is a hint for the NEXT project."""
        reg = write_registry(self.root, {"registry": {}, "next_available": 8801})
        with self.assertRaises(portlib.PortError) as e:
            portlib.resolve_port(KEY, registry=reg)
        self.assertNotIn("8801", str(e.exception),
                         "must not even suggest next_available as this port")

    def test_no_default_means_hard_failure(self):
        reg = write_registry(self.root, {"registry": {}})
        with self.assertRaises(portlib.PortError):
            portlib.resolve_port(KEY, registry=reg)

    def test_error_says_how_to_fix_it(self):
        with self.assertRaises(portlib.PortError) as e:
            portlib.resolve_port(KEY, start=self.root)
        msg = str(e.exception)
        self.assertIn(KEY, msg)
        self.assertIn("--port", msg)
        self.assertIn("ports.json", msg)

    def test_malformed_registry_is_reported_not_ignored(self):
        p = self.root / "ports.json"
        p.write_text("{ broken", encoding="utf-8")
        with self.assertRaises(portlib.PortError):
            portlib.resolve_port(KEY, default=8888, registry=p)

    def test_non_numeric_port_is_reported(self):
        reg = write_registry(self.root, {"registry": {KEY: {"port": "eight"}}})
        with self.assertRaises(portlib.PortError):
            portlib.resolve_port(KEY, registry=reg)

    def test_bad_env_value_is_reported_not_skipped(self):
        reg = write_registry(self.root, full(8790))
        with mock.patch.dict(os.environ, {"DEMO_PORT": "not-a-port"}):
            with self.assertRaises(portlib.PortError) as e:
                portlib.resolve_port(KEY, env_var="DEMO_PORT", registry=reg)
        self.assertIn("silently ignored", str(e.exception))

    def test_entry_without_a_port_falls_through(self):
        reg = write_registry(self.root, {"registry": {KEY: {"status": "planned"}}})
        self.assertEqual(portlib.resolve_port(KEY, default=8888, registry=reg), 8888)


class TestRegistryDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_walks_up_to_find_it(self):
        reg = write_registry(self.root, full(8790))
        deep = self.root / "repo" / "src" / "pkg"
        deep.mkdir(parents=True)
        self.assertEqual(portlib.find_registry(deep), reg)

    def test_nearest_wins(self):
        write_registry(self.root, full(1111))
        repo = self.root / "repo"
        repo.mkdir()
        near = write_registry(repo, full(2222))
        self.assertEqual(portlib.find_registry(repo), near)
        self.assertEqual(portlib.resolve_port(KEY, start=repo), 2222)

    def test_gives_up_beyond_the_depth_limit(self):
        write_registry(self.root, full(8790))
        deep = self.root / "a" / "b" / "c" / "d" / "e" / "f"
        deep.mkdir(parents=True)
        self.assertIsNone(portlib.find_registry(deep, depth=2))

    def test_absent_registry_is_none_not_an_error(self):
        self.assertIsNone(portlib.find_registry(self.root))


class TestNoPortHunting(unittest.TestCase):
    def test_returns_the_registered_port_even_when_it_is_busy(self):
        """Reverse proxies and service discovery attribute a service BY PORT,
        so drifting to port+1 makes them mislabel it. Failing to bind loudly is
        the correct outcome; quietly moving is not."""
        import socket
        with TemporaryDirectory() as t:
            reg = write_registry(Path(t), full(0))
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            busy = s.getsockname()[1]
            try:
                reg = write_registry(Path(t), full(busy))
                self.assertEqual(portlib.resolve_port(KEY, registry=reg), busy,
                                 "resolution must not care whether it is free")
            finally:
                s.close()

    def test_code_never_touches_next_available(self):
        """Checked structurally: the words appear in the docstring explaining
        why we don't read it, which must not itself trip the check."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(portlib))
        doc = ast.get_docstring(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value == doc or "never reads" in node.value:
                    continue
                self.assertNotIn("next_available", node.value,
                                 "code must not reference next_available")

    def test_code_has_no_retry_or_scan_loop(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(portlib))
        loops = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.While, ast.For))]
        # The only loop allowed is the upward registry search in find_registry.
        self.assertLessEqual(len(loops), 1, "extra loops suggest port hunting")
        self.assertFalse([n for n in ast.walk(tree) if isinstance(n, ast.While)],
                         "a while loop here would mean retrying")


if __name__ == "__main__":
    unittest.main()

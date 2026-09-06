"""Tests for atomic_write.py -- the shared helper behind every JSON/text file
save in the app (finances.json via server.py, investlib's per-collection
files via investlib/store.py).

Regression context: Windows can raise PermissionError from os.replace() when
something else (an AV scanner, the indexer, a not-yet-closed handle) briefly
holds the temp or target file open. These tests prove: a normal round trip
works, a transient replace failure is retried and recovers, a permanent
failure raises loudly while leaving the original file and no stray temp file
behind, and concurrent writers never corrupt the target or leak temp files.

Run from the project root:
    python -m unittest discover -s tests -p "test_*.py" -v
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import atomic_write  # noqa: E402
from atomic_write import (  # noqa: E402
    AtomicWriteError,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)


def _tmp_files_in(directory):
    return [n for n in os.listdir(directory) if n.endswith(".tmp")]


class AtomicWriteRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.target = os.path.join(self.dir, "finances.json")

    def tearDown(self):
        self._td.cleanup()

    def test_json_round_trip_creates_file_with_no_stray_tmp(self):
        data = {"settings": {"appName": "Test"}, "loans": [1, 2, 3]}
        atomic_write_json(self.target, data)
        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), data)
        self.assertEqual(_tmp_files_in(self.dir), [])

    def test_text_round_trip_overwrites_existing_content(self):
        with open(self.target, "w", encoding="utf-8") as f:
            f.write("old contents")
        atomic_write_text(self.target, "new contents")
        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "new contents")
        self.assertEqual(_tmp_files_in(self.dir), [])

    def test_bytes_round_trip(self):
        atomic_write_bytes(self.target, b"\x00\x01binary")
        with open(self.target, "rb") as f:
            self.assertEqual(f.read(), b"\x00\x01binary")
        self.assertEqual(_tmp_files_in(self.dir), [])

    def test_temp_file_is_written_in_same_directory_as_target(self):
        """Same-directory temp file is what makes the final os.replace() a
        same-volume, atomic rename on both POSIX and Windows."""
        seen_dirs = []
        real_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            seen_dirs.append(kwargs.get("dir"))
            return real_mkstemp(*args, **kwargs)

        with mock.patch("atomic_write.tempfile.mkstemp", side_effect=spy_mkstemp):
            atomic_write_json(self.target, {"a": 1})
        self.assertEqual(seen_dirs, [self.dir])


class AtomicWriteRetryTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.target = os.path.join(self.dir, "finances.json")

    def tearDown(self):
        self._td.cleanup()

    def test_transient_replace_failure_retries_then_succeeds(self):
        """First two os.replace() calls raise the classic Windows sharing-violation
        PermissionError; the third (real) call goes through. The write must
        succeed overall, and no stray .tmp file should remain."""
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append((src, dst))
            if len(calls) < 3:
                raise PermissionError(13, "The process cannot access the file "
                                           "because it is being used by another process")
            return real_replace(src, dst)

        with mock.patch("atomic_write.os.replace", side_effect=flaky_replace):
            atomic_write_json(self.target, {"n": 42}, retries=5, initial_delay=0.001)

        self.assertEqual(len(calls), 3, "should have retried twice before succeeding")
        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"n": 42})
        self.assertEqual(_tmp_files_in(self.dir), [])

    def test_permanent_replace_failure_raises_and_preserves_original(self):
        """Every os.replace() attempt fails. atomic_write must: raise
        AtomicWriteError chained from the original error, leave the
        pre-existing target file byte-for-byte untouched, and remove the
        dead temp file (never leak a .tmp)."""
        original = json.dumps({"settings": {}, "loans": ["do-not-lose-me"]})
        with open(self.target, "w", encoding="utf-8") as f:
            f.write(original)

        def always_fails(src, dst):
            raise PermissionError(13, "used by another process")

        with mock.patch("atomic_write.os.replace", side_effect=always_fails):
            with self.assertRaises(AtomicWriteError) as ctx:
                atomic_write_json(self.target, {"settings": {}, "loans": []},
                                   retries=3, initial_delay=0.001)

        # Original error is chained, never swallowed.
        self.assertIsInstance(ctx.exception.__cause__, PermissionError)
        # Previous contents survive completely intact.
        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), original)
        # No stray temp file left behind.
        self.assertEqual(_tmp_files_in(self.dir), [])

    def test_permanent_failure_when_target_never_existed_leaves_no_tmp(self):
        """Same permanent-failure scenario, but the target file never existed
        (first-ever save). Must raise, and must not leave a stray temp file
        or a partially-written target."""
        def always_fails(src, dst):
            raise PermissionError(13, "used by another process")

        with mock.patch("atomic_write.os.replace", side_effect=always_fails):
            with self.assertRaises(AtomicWriteError):
                atomic_write_json(self.target, {"n": 1}, retries=2, initial_delay=0.001)

        self.assertFalse(os.path.exists(self.target))
        self.assertEqual(os.listdir(self.dir), [])


class AtomicWriteConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.target = os.path.join(self.dir, "finances.json")
        atomic_write_json(self.target, {"n": -1})  # seed an initial file

    def tearDown(self):
        self._td.cleanup()

    def test_concurrent_writers_never_corrupt_file_or_leak_tmp_files(self):
        """20 threads hammer atomic_write_json on the SAME target concurrently
        (no external lock -- exercising the helper's own guarantees, not the
        server's file_lock). The file must stay valid JSON after the storm,
        matching whichever writer replaced it last, and no writer's temp
        file may survive."""
        errors = []

        def writer(n):
            try:
                atomic_write_json(self.target, {"n": n})
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], "no writer should raise")
        with open(self.target, "r", encoding="utf-8") as f:
            got = json.load(f)
        self.assertIn("n", got)
        self.assertIn(got["n"], list(range(20)))
        self.assertEqual(_tmp_files_in(self.dir), [],
                          "no writer's temp file should survive the storm")
        self.assertEqual(sorted(os.listdir(self.dir)), ["finances.json"])


if __name__ == "__main__":
    unittest.main()

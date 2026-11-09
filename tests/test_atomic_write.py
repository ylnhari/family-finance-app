"""Tests for atomic_write.py -- the shared helper behind every JSON/text file
save in the app (finances.json via server.py, investlib's per-collection
files via investlib/store.py).

Regression context: Windows can raise PermissionError from os.replace() when
something else (an AV scanner, the indexer, a not-yet-closed handle) briefly
holds the temp or target file open. These tests prove: a normal round trip
works, a transient replace failure is retried and recovers, a permanent
failure raises loudly while leaving the original file and no stray temp file
behind, and concurrent writers never corrupt the target or leak temp files.

Windows also refuses a replace of a file that another replace has not finished
with, with the very same PermissionError. That made twenty threads saving one
file fail intermittently on Windows CI (the retry loop made every loser come
back at the same moment and collide again). The helper now serialises
same-process replaces of one target and randomises its backoff; the classes
AtomicWriteSerialisationTests and AtomicWriteBackoffTests prove both
deterministically, on every OS, by modelling the Windows rule instead of
depending on how the local OS happens to schedule real renames.

Run from the project root:
    python -m unittest discover -s tests -p "test_*.py" -v
"""
import json
import os
import sys
import tempfile
import threading
import time
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


class _WindowsLikeReplace:
    """Stand-in for os.replace that obeys the Windows rule behind the CI flake:
    a replace onto a destination that another replace has not finished with is
    refused with PermissionError(13, 'Access is denied'). Because the rule is
    modelled rather than left to the local OS, tests using this are
    deterministic everywhere (Linux CI included).

    `hold(destination)` runs while a replace is "in flight", so a test can keep
    one there or simply widen the window. `refused` lists the refusals and
    `succeeded` the (destination, payload) of every completed replace, in order.
    """

    def __init__(self, hold=None):
        self._real = os.replace  # captured before any patching
        self._guard = threading.Lock()
        self._in_flight = set()
        self.hold = hold
        self.refused = []
        self.refused_event = threading.Event()
        self.succeeded = []

    @staticmethod
    def _identity(path):
        return os.path.normcase(os.path.realpath(path))

    def __call__(self, src, dst):
        who = self._identity(dst)
        with self._guard:
            if who in self._in_flight:
                self.refused.append(who)
                self.refused_event.set()
                raise PermissionError(13, "Access is denied")
            self._in_flight.add(who)
        try:
            if self.hold is not None:
                self.hold(who)
            with open(src, "r", encoding="utf-8") as f:
                payload = f.read()  # the temp file holds this writer's content
            self._real(src, dst)
            with self._guard:
                self.succeeded.append((who, payload))
        finally:
            with self._guard:
                self._in_flight.discard(who)


class _FixedRng:
    """Stand-in for atomic_write._rng: uniform(a, b) returns the point
    `fraction` of the way from a to b (0 = lower bound, 1 = upper bound)."""

    def __init__(self, fraction):
        self.fraction = fraction

    def uniform(self, a, b):
        return a + (b - a) * self.fraction


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
        server's file_lock) through the REAL os.replace. Every writer must
        succeed, the file must stay valid JSON holding exactly what the writer
        whose replace came last wrote, and no writer's temp file may survive.

        This is the scenario that failed intermittently on Windows CI with
        AtomicWriteError(PermissionError(13, 'Access is denied')): Windows
        refuses a replace that overlaps another replace of the same file. It no
        longer depends on timing because the helper serialises same-process
        replaces of one target itself; AtomicWriteSerialisationTests proves
        that without relying on the local OS."""
        real_replace = os.replace
        replaced = []  # payload of each successful replace, in the order they happened

        def recording_replace(src, dst):
            with open(src, "r", encoding="utf-8") as f:
                payload = f.read()
            real_replace(src, dst)
            replaced.append(payload)

        errors = []

        def writer(n):
            try:
                atomic_write_json(self.target, {"n": n})
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        with mock.patch("atomic_write.os.replace", new=recording_replace):
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(errors, [], "no writer should raise")
        with open(self.target, "r", encoding="utf-8") as f:
            got = json.load(f)
        self.assertIn("n", got)
        self.assertIn(got["n"], list(range(20)))
        self.assertEqual(len(replaced), 20, "every writer replaces the file exactly once")
        self.assertEqual(got, json.loads(replaced[-1]),
                          "the file must hold what the last replace wrote (no lost last write)")
        self.assertEqual(_tmp_files_in(self.dir), [],
                          "no writer's temp file should survive the storm")
        self.assertEqual(sorted(os.listdir(self.dir)), ["finances.json"])


class AtomicWriteSerialisationTests(unittest.TestCase):
    """Threads of one process never replace the same file at the same moment.

    Deterministic: they use _WindowsLikeReplace and Events instead of racing
    real renames, and every wait that can only time out when the code is
    correct is one-sided (it can report a bug, never invent one)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.target = os.path.join(self.dir, "finances.json")

    def tearDown(self):
        self._td.cleanup()

    def _spawn_writer(self, target, payload, errors):
        def run():
            try:
                atomic_write_json(target, payload, retries=3, initial_delay=0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
        t = threading.Thread(target=run)
        t.start()
        return t

    def test_second_writer_waits_while_first_is_replacing_the_same_file(self):
        first_inside = threading.Event()
        release_first = threading.Event()
        held = []

        def hold(_who):
            if not held:
                held.append(True)
                first_inside.set()
                release_first.wait(10)

        fake = _WindowsLikeReplace(hold=hold)
        errors = []
        threads = []
        with mock.patch("atomic_write.os.replace", new=fake):
            try:
                threads.append(self._spawn_writer(self.target, {"n": 1}, errors))
                self.assertTrue(first_inside.wait(10), "first writer never reached os.replace")
                # Same file, different spelling: must still be treated as one target.
                same_file = os.path.join(self.dir, ".", "finances.json")
                threads.append(self._spawn_writer(same_file, {"n": 2}, errors))
                # A correct helper keeps the second writer out of os.replace until
                # the first has finished, so this wait normally runs to its timeout;
                # it only returns early when the second writer collided.
                self.assertFalse(fake.refused_event.wait(0.5),
                                 "second writer entered os.replace while the first was "
                                 "still replacing the same file")
            finally:
                release_first.set()
                for t in threads:
                    t.join(10)

        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(fake.refused, [])
        # The second writer could only run after the first, so its data wins.
        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"n": 2})
        self.assertEqual(os.listdir(self.dir), ["finances.json"])

    def test_writers_to_different_files_do_not_wait_for_each_other(self):
        other = os.path.join(self.dir, "holdings.json")
        first_inside = threading.Event()
        release_first = threading.Event()
        blocked_identity = _WindowsLikeReplace._identity(self.target)

        def hold(who):
            if who == blocked_identity:
                first_inside.set()
                release_first.wait(10)

        fake = _WindowsLikeReplace(hold=hold)
        errors = []
        threads = []
        with mock.patch("atomic_write.os.replace", new=fake):
            try:
                threads.append(self._spawn_writer(self.target, {"n": 1}, errors))
                self.assertTrue(first_inside.wait(10), "first writer never reached os.replace")
                unrelated = self._spawn_writer(other, {"n": 2}, errors)
                threads.append(unrelated)
                unrelated.join(5)
                self.assertFalse(unrelated.is_alive(),
                                 "a writer to a different file was stuck behind this one")
            finally:
                release_first.set()
                for t in threads:
                    t.join(10)

        self.assertEqual(errors, [])
        self.assertEqual(fake.refused, [])
        for name, want in (("finances.json", {"n": 1}), ("holdings.json", {"n": 2})):
            with open(os.path.join(self.dir, name), "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f), want)

    def test_backoff_sleep_does_not_hold_the_target_lock(self):
        """A writer waiting out a transient lock (antivirus, a reader) must not
        stall other writers of the same file while it sleeps."""
        real_replace = os.replace
        a_sleeping = threading.Event()
        b_done = threading.Event()
        calls = []
        errors = []

        def replace(src, dst):
            calls.append(src)
            if len(calls) == 1:  # only the first writer is running at this point
                raise PermissionError(13, "Access is denied")
            real_replace(src, dst)

        def pause(_seconds):
            a_sleeping.set()
            b_done.wait(10)  # stay "asleep" until the second writer has finished

        def writer_a():
            try:
                atomic_write_json(self.target, {"n": "a"}, retries=3, initial_delay=0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        def writer_b():
            try:
                atomic_write_json(self.target, {"n": "b"})
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
            finally:
                b_done.set()

        a = threading.Thread(target=writer_a)
        b = threading.Thread(target=writer_b)
        with mock.patch("atomic_write.os.replace", new=replace), \
                mock.patch("atomic_write.time.sleep", new=pause):
            try:
                a.start()
                self.assertTrue(a_sleeping.wait(10), "first writer never backed off")
                b.start()
            finally:
                a.join(15)
                b.join(15)

        self.assertEqual(errors, [])
        self.assertTrue(b_done.is_set())
        # b replaced the file while a slept; a's retry then replaced it last.
        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"n": "a"})
        self.assertEqual(os.listdir(self.dir), ["finances.json"])

    def test_twenty_simultaneous_writers_never_collide_and_the_last_replace_wins(self):
        """The CI storm, on a stand-in for Windows' rule: with the default retry
        budget, twenty writers released at the same instant must not be refused
        even once, and the file must hold what the last replace wrote."""
        fake = _WindowsLikeReplace(hold=lambda _who: time.sleep(0.002))  # widen the window
        start_together = threading.Barrier(20)
        errors = []

        def writer(n):
            try:
                start_together.wait(10)
                atomic_write_json(self.target, {"n": n})
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        with mock.patch("atomic_write.os.replace", new=fake):
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)

        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [], "no writer should raise")
        self.assertEqual(fake.refused, [], "no replace may start while another is in flight")
        self.assertEqual(len(fake.succeeded), 20)
        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), fake.succeeded[-1][1],
                             "the file must hold what the last replace wrote")
        self.assertEqual(os.listdir(self.dir), ["finances.json"])
        self.assertEqual(atomic_write._target_locks, {}, "lock table must not leak entries")

    def test_lock_table_is_empty_after_success_and_after_failure(self):
        atomic_write_json(self.target, {"n": 1})
        self.assertEqual(atomic_write._target_locks, {})

        def always_fails(src, dst):
            raise PermissionError(13, "Access is denied")

        with mock.patch("atomic_write.os.replace", new=always_fails), \
                mock.patch("atomic_write.time.sleep"):
            with self.assertRaises(AtomicWriteError):
                atomic_write_json(self.target, {"n": 2}, retries=2)
        self.assertEqual(atomic_write._target_locks, {})
        self.assertEqual(_tmp_files_in(self.dir), [])


class AtomicWriteBackoffTests(unittest.TestCase):
    """The retry schedule: how many attempts, how long the pauses are, and that
    the pauses are randomised so writers that collided drift apart instead of
    colliding again in lock-step. time.sleep is replaced, so nothing waits."""

    NOMINAL = [0.05, 0.1, 0.2, 0.4, 0.8]  # default schedule for the 6 default attempts

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.target = os.path.join(self._td.name, "finances.json")

    def tearDown(self):
        self._td.cleanup()

    def _failing_write(self):
        """One default-budget write whose os.replace always fails.
        Returns (number of attempts, list of pauses)."""
        attempts = []
        sleeps = []

        def always_fails(src, dst):
            attempts.append(1)
            raise PermissionError(13, "Access is denied")

        with mock.patch("atomic_write.os.replace", new=always_fails), \
                mock.patch("atomic_write.time.sleep", new=sleeps.append):
            with self.assertRaises(AtomicWriteError):
                atomic_write_json(self.target, {"n": 1})
        return len(attempts), sleeps

    def test_budget_is_six_attempts_with_doubling_pauses_between_them(self):
        # Zero jitter draw: exactly the plain exponential schedule.
        with mock.patch.object(atomic_write, "_rng", _FixedRng(0.0)):
            attempts, sleeps = self._failing_write()
        self.assertEqual(attempts, atomic_write.DEFAULT_RETRIES)
        self.assertEqual(len(sleeps), atomic_write.DEFAULT_RETRIES - 1,
                         "no pause after the final attempt")
        for got, want in zip(sleeps, self.NOMINAL):
            self.assertAlmostEqual(got, want)

    def test_pauses_are_lengthened_by_at_most_the_documented_fraction(self):
        jitter = atomic_write.JITTER
        self.assertGreater(jitter, 0, "no jitter means colliding writers retry in lock-step")
        for fraction, factor in ((0.0, 1.0), (1.0, 1 + jitter)):
            with self.subTest(draw="lowest" if fraction == 0 else "highest"):
                with mock.patch.object(atomic_write, "_rng", _FixedRng(fraction)):
                    _, sleeps = self._failing_write()
                for got, want in zip(sleeps, self.NOMINAL):
                    self.assertAlmostEqual(got, want * factor)

    def test_real_jitter_never_shortens_a_pause_and_actually_varies(self):
        jitter = atomic_write.JITTER
        first_pauses = set()
        for _ in range(30):
            _, sleeps = self._failing_write()
            first_pauses.add(sleeps[0])
            for got, want in zip(sleeps, self.NOMINAL):
                # Never less patient than the plain exponential schedule...
                self.assertGreaterEqual(got, want)
                # ...and never more than the documented fraction longer.
                self.assertLessEqual(got, want * (1 + jitter))
        self.assertGreater(len(first_pauses), 1,
                           "identical pauses would make colliding writers retry in lock-step")


if __name__ == "__main__":
    unittest.main()

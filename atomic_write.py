"""One shared helper for atomic, Windows-safe JSON/text file writes.

Every place in this app that persists a JSON document (`finances.json`,
investlib's per-collection files under `data/invest/`) used to hand-roll the
same "write a `.tmp` file, then `os.replace()` it onto the target" dance. On
Windows, `os.replace()` (`MoveFileEx` under the hood) can raise
`PermissionError` when something else — a virus scanner, the search
indexer, a not-yet-closed handle on the temp file — briefly holds the temp
or target file open. That is almost always transient: retrying a moment
later succeeds. The old call sites did not retry, so a family member adding
a salary row could hit a bare `PermissionError: [Errno 13]` and lose the
save.

This module is the ONE place that performs the write, so the fix (and the
retry policy) lives here instead of being copy-pasted at every call site:

  1. Write the new content to a uniquely-named temp file in the SAME
     directory as the target (so the final `os.replace()` is same-volume
     and atomic), then flush + `os.fsync()` + close it before ever touching
     the target.
  2. `os.replace()` the temp file onto the target, retrying a bounded number
     of times with a short exponential backoff if that raises `OSError`
     (covers Windows sharing violations: `PermissionError` / `WinError 5`
     "Access is denied" / `WinError 32` "used by another process").
     Two details stop that retry loop from defeating itself:
       * Threads of this process never run `os.replace()` onto the SAME target
         path at the same moment: a per-target lock is held around each
         individual call (never around the backoff sleeps). Windows rejects a replace of
         a file that another replace is still in the middle of with exactly
         the `PermissionError` above, so unserialised writers used to cause
         their own failures. With twenty threads saving one file, a writer
         could use up its whole retry budget on a busy machine and raise.
       * Every backoff sleep is lengthened by a random 0-50% ("jitter") on top
         of its nominal exponential length. Contenders that do collide
         (another process, an antivirus scan, a reader holding the file open)
         therefore wake up at different moments instead of colliding again in
         lock-step. No pause is ever shorter than the plain exponential
         schedule, so a transient lock that schedule would have outlasted is
         still outlasted.
     The lock only covers threads of one process. Writers in different
     processes (for example the server and a scheduled script) are separated
     by the retry loop alone, and `finances.json` additionally by the
     server's `file_lock`.
  3. If every retry is exhausted, raise `AtomicWriteError` (chained from the
     original exception — nothing is swallowed) and remove the now-useless
     temp file. The target file is never opened for writing directly, so a
     failed replace leaves the previous contents exactly as they were: nothing
     is truncated, nothing is lost, and no stray `.tmp` file is left behind.
"""
import contextlib
import json
import os
import random
import tempfile
import threading
import time

DEFAULT_RETRIES = 6
DEFAULT_INITIAL_DELAY = 0.05  # seconds; doubles each retry
# Each backoff sleep is its nominal length times uniform(1, 1 + JITTER): never
# shorter than the plain exponential schedule, so only the synchronisation
# between colliding writers is broken, never the patience.
JITTER = 0.5

# SystemRandom asks the OS for entropy on every draw, so there is no shared
# seed: two processes (or a test that calls random.seed()) can never end up
# with identical "random" backoff sequences, which would defeat the jitter.
_rng = random.SystemRandom()

# One lock per target file that a thread of this process is currently trying to
# replace: key -> [lock, number of threads holding or waiting for it]. An entry
# exists only while somebody uses it, so the table cannot grow without bound.
_target_locks = {}
_target_locks_guard = threading.Lock()


class AtomicWriteError(OSError):
    """The final os.replace() kept failing after every retry. The target
    file (if it existed) was never touched; the temp file has been removed."""


def atomic_write_text(target, text, encoding="utf-8", retries=DEFAULT_RETRIES,
                       initial_delay=DEFAULT_INITIAL_DELAY):
    """Atomically replace `target` with `text`. See module docstring."""
    def _write(f):
        f.write(text)
    _atomic_write(target, _write, binary=False, encoding=encoding,
                  retries=retries, initial_delay=initial_delay)


def atomic_write_bytes(target, data, retries=DEFAULT_RETRIES,
                        initial_delay=DEFAULT_INITIAL_DELAY):
    """Atomically replace `target` with raw bytes `data`."""
    def _write(f):
        f.write(data)
    _atomic_write(target, _write, binary=True, encoding=None,
                  retries=retries, initial_delay=initial_delay)


def atomic_write_json(target, data, indent=2, ensure_ascii=False,
                       retries=DEFAULT_RETRIES, initial_delay=DEFAULT_INITIAL_DELAY):
    """Atomically replace `target` with `json.dumps(data)`. This is the
    shape every JSON-file writer in the app wants (finances.json,
    investlib's per-collection files) — use this one, not a bare
    open+json.dump+os.replace at the call site."""
    text = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    atomic_write_text(target, text, retries=retries, initial_delay=initial_delay)


def _atomic_write(target, write_fn, binary, encoding, retries, initial_delay):
    target = os.fspath(target)
    target_dir = os.path.dirname(os.path.abspath(target)) or "."
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(target) + ".",
        suffix=".tmp",
        dir=target_dir,
    )
    try:
        f = os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding=encoding)
        try:
            write_fn(f)
            f.flush()
            os.fsync(f.fileno())
        finally:
            f.close()
        _replace_with_retry(tmp, target, retries, initial_delay)
    finally:
        # Whether we succeeded (tmp no longer exists, this is a no-op) or
        # every retry failed (tmp is now dead weight) — never leave a stray
        # temp file behind.
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _lock_key(target):
    # Lexical normalisation only. Resolving symlinks or 8.3 short names would
    # mean opening the target (or its directory) just to build a dictionary
    # key, and on Windows an extra open is itself a way to make a concurrent
    # replace fail. Every caller in the app spells a given target the same way.
    return os.path.normcase(os.path.abspath(target))


@contextlib.contextmanager
def _target_lock(key):
    """Mutual exclusion between threads of this process that are replacing
    the same file. Reference-counted so an idle target leaves no entry."""
    with _target_locks_guard:
        entry = _target_locks.get(key)
        if entry is None:
            entry = _target_locks[key] = [threading.Lock(), 0]
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _target_locks_guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _target_locks[key]


def _jittered(delay):
    """`delay` lengthened by a random 0..JITTER fraction so that writers which
    collided do not all come back at the same instant and collide again."""
    return delay * _rng.uniform(1.0, 1.0 + JITTER)


def _replace_with_retry(tmp, target, retries, initial_delay):
    key = _lock_key(target)
    delay = initial_delay
    last_err = None
    for attempt in range(retries):
        try:
            # The lock covers the single os.replace() call only. It is released
            # before any backoff sleep, so a writer waiting out a transient
            # lock (antivirus, a reader) never stalls the other writers.
            with _target_lock(key):
                os.replace(tmp, target)
            return
        except OSError as e:
            last_err = e
            if attempt == retries - 1:
                break
            time.sleep(_jittered(delay))
            delay *= 2
    # Fail loudly — never swallow the original error — and leave the
    # previous contents of `target` (if any) completely untouched.
    raise AtomicWriteError(
        "Could not replace %r with the new contents (tmp file %r) after "
        "%d attempt(s). Original error: %r" % (target, tmp, retries, last_err)
    ) from last_err

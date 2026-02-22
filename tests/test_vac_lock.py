"""Tests for scripts/vac_lock.py."""

import json
import os
import subprocess
import sys
import time

import pytest

# Add scripts dir to path so we can import vac_lock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from vac_lock import (
    vac_lock,
    LOCK_FILE,
    _read_lock,
    _is_stale,
    _write_lock,
    _remove_lock,
    _pid_alive,
)

# Short timeouts for tests
FAST_TIMEOUT = 3
FAST_POLL = 0.2


@pytest.fixture(autouse=True)
def clean_lock():
    """Remove lock file before and after each test."""
    try:
        os.remove(LOCK_FILE)
    except (FileNotFoundError, OSError):
        pass
    yield
    try:
        os.remove(LOCK_FILE)
    except (FileNotFoundError, OSError):
        pass


def _dead_pid():
    """Return a PID that is guaranteed to be dead.

    Spawns a short-lived process and waits for it to finish.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc.wait()
    return proc.pid


class TestAcquireRelease:
    def test_basic_acquire_release(self):
        with vac_lock(timeout=FAST_TIMEOUT, poll_interval=FAST_POLL):
            lock_info = _read_lock()
            assert lock_info is not None
            assert lock_info["pid"] == os.getpid()
        # Lock file should be gone after release
        assert _read_lock() is None

    def test_lock_file_content(self):
        with vac_lock(timeout=FAST_TIMEOUT, poll_interval=FAST_POLL):
            lock_info = _read_lock()
            assert "pid" in lock_info
            assert "timestamp" in lock_info
            assert "cwd" in lock_info
            assert "argv" in lock_info
            assert isinstance(lock_info["timestamp"], float)

    def test_reacquire_after_release(self):
        with vac_lock(timeout=FAST_TIMEOUT, poll_interval=FAST_POLL):
            pass
        with vac_lock(timeout=FAST_TIMEOUT, poll_interval=FAST_POLL):
            lock_info = _read_lock()
            assert lock_info["pid"] == os.getpid()


class TestStaleLock:
    def test_dead_pid_is_stale(self):
        dead = _dead_pid()
        info = {
            "pid": dead,
            "timestamp": time.time(),
            "cwd": "/tmp",
            "argv": [],
        }
        with open(LOCK_FILE, "w") as f:
            json.dump(info, f)

        assert _is_stale(info) is True

    def test_expired_timestamp_is_stale(self):
        info = {
            "pid": os.getpid(),  # Alive, but very old
            "timestamp": time.time() - 9999,
            "cwd": "/tmp",
            "argv": [],
        }
        with open(LOCK_FILE, "w") as f:
            json.dump(info, f)

        assert _is_stale(info) is True

    def test_current_lock_is_not_stale(self):
        info = {
            "pid": os.getpid(),
            "timestamp": time.time(),
            "cwd": "/tmp",
            "argv": [],
        }
        assert _is_stale(info) is False

    def test_acquire_removes_stale_lock(self):
        dead = _dead_pid()
        info = {
            "pid": dead,
            "timestamp": time.time(),
            "cwd": "/tmp",
            "argv": [],
        }
        with open(LOCK_FILE, "w") as f:
            json.dump(info, f)

        # Should succeed (stale lock removed)
        with vac_lock(timeout=FAST_TIMEOUT, poll_interval=FAST_POLL):
            lock_info = _read_lock()
            assert lock_info["pid"] == os.getpid()

    def test_acquire_removes_expired_lock(self):
        # Plant a lock with expired timestamp (our own PID, but old)
        info = {
            "pid": os.getpid(),
            "timestamp": time.time() - 9999,
            "cwd": "/tmp",
            "argv": [],
        }
        with open(LOCK_FILE, "w") as f:
            json.dump(info, f)

        with vac_lock(timeout=FAST_TIMEOUT, poll_interval=FAST_POLL):
            lock_info = _read_lock()
            # Fresh timestamp
            assert lock_info["timestamp"] > info["timestamp"]


class TestPidAlive:
    def test_own_pid_alive(self):
        assert _pid_alive(os.getpid()) is True

    def test_dead_pid_not_alive(self):
        dead = _dead_pid()
        assert _pid_alive(dead) is False


class TestTimeout:
    def test_timeout_raises(self):
        # Plant a non-stale lock from a live process.
        # Use our own PID with current timestamp — the lock looks valid
        # but we can't acquire it because it's "held" by someone.
        # We need a PID that is alive but isn't us. Use a long-running
        # subprocess.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            info = {
                "pid": proc.pid,
                "timestamp": time.time(),
                "cwd": "/tmp",
                "argv": [],
            }
            with open(LOCK_FILE, "w") as f:
                json.dump(info, f)

            with pytest.raises(TimeoutError):
                with vac_lock(timeout=1, poll_interval=FAST_POLL):
                    pass
        finally:
            proc.kill()
            proc.wait()

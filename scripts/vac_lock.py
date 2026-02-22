"""Cross-process lock for VAC test scripts.

Prevents multiple worktrees/terminals from fighting over shared
audio devices (indices 3, 5, 8, 11) and port 8080.
"""

import json
import os
import sys
import tempfile
import time

LOCK_FILE = os.path.join(tempfile.gettempdir(), "modumb-vac-test.lock")
DEFAULT_TIMEOUT = 300  # 5 minutes
POLL_INTERVAL = 5      # seconds
STALE_AGE = 600        # 10 minutes


def _pid_alive(pid):
    """Check if a process is alive (cross-platform)."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _read_lock():
    """Read lock file contents, return dict or None."""
    try:
        with open(LOCK_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _is_stale(lock_info):
    """Check if a lock is stale (dead PID or too old)."""
    if lock_info is None:
        return True
    pid = lock_info.get("pid")
    ts = lock_info.get("timestamp", 0)
    if not _pid_alive(pid):
        return True
    if time.time() - ts > STALE_AGE:
        return True
    return False


def _write_lock():
    """Write our lock file."""
    info = {
        "pid": os.getpid(),
        "timestamp": time.time(),
        "cwd": os.getcwd(),
        "argv": sys.argv,
    }
    with open(LOCK_FILE, "w") as f:
        json.dump(info, f)


def _remove_lock():
    """Remove lock file if it belongs to us."""
    lock_info = _read_lock()
    if lock_info and lock_info.get("pid") == os.getpid():
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


class vac_lock:
    """Context manager for VAC test lock.

    Usage:
        with vac_lock():
            # run VAC tests
    """

    def __init__(self, timeout=DEFAULT_TIMEOUT, poll_interval=POLL_INTERVAL):
        self.timeout = timeout
        self.poll_interval = poll_interval

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            lock_info = _read_lock()
            if lock_info is None or _is_stale(lock_info):
                if lock_info is not None:
                    print(f"Removing stale lock (pid={lock_info.get('pid')})",
                          file=sys.stderr, flush=True)
                    try:
                        os.remove(LOCK_FILE)
                    except OSError:
                        pass
                _write_lock()
                print("VAC lock acquired", file=sys.stderr, flush=True)
                return self

            if time.time() >= deadline:
                raise TimeoutError(
                    f"Could not acquire VAC lock after {self.timeout}s "
                    f"(held by pid={lock_info.get('pid')})"
                )

            print(f"Waiting for VAC lock held by pid={lock_info.get('pid')} ...",
                  file=sys.stderr, flush=True)
            time.sleep(self.poll_interval)

    def __exit__(self, exc_type, exc_val, exc_tb):
        _remove_lock()
        print("VAC lock released", file=sys.stderr, flush=True)
        return False

"""Keep the machine quiet while timing.

* ``HeavyLock``: one timing job at a time. Every ``speedloop bench`` holds it for its whole schedule, and
  ``speedloop locked -- CMD`` runs anything else (a test suite, a build) under the same lock, so nothing
  heavy runs beside a measurement. The lock is a file lock, released automatically if the holder dies.
* ``cpu_busy``: how busy the CPUs are right now (0-100 %), system-wide. ``bench`` samples it before every
  run, waits while the machine is busy, and records the value in the receipt.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def default_lock_path() -> Path:
    return Path(os.environ.get("SPEEDLOOP_LOCK") or Path.home() / ".speedloop" / "heavy.lock")


class LockTimeout(TimeoutError):
    pass


class HeavyLock:
    def __init__(self, path: Path | None = None, timeout: float | None = None, poll: float = 0.5, progress=None):
        self.path = Path(path) if path else default_lock_path()
        self.timeout, self.poll, self.progress = timeout, poll, progress
        self._fd = None

    def _try(self) -> bool:
        if sys.platform == "win32":
            import msvcrt
            try:
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        import fcntl
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        started, told = time.monotonic(), False
        while not self._try():
            if self.timeout is not None and time.monotonic() - started > self.timeout:
                os.close(self._fd)
                self._fd = None
                raise LockTimeout(f"{self.path} still held after {self.timeout:.0f} s")
            if self.progress and not told:
                self.progress(f"waiting for the heavy-job lock {self.path} ...")
                told = True
            time.sleep(self.poll)

    def release(self) -> None:
        if self._fd is None:
            return
        if sys.platform == "win32":
            import msvcrt
            os.lseek(self._fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


# --- how busy is the machine ---------------------------------------------------------------------------

def _cpu_times() -> tuple[float, float] | None:
    """(busy, total) cumulative CPU time, or None when unavailable."""
    if sys.platform.startswith("linux"):
        with open("/proc/stat", encoding="ascii") as f:
            parts = [float(x) for x in f.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0.0)  # idle + iowait
        total = sum(parts[:8])
        return total - idle, total
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        val = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime  # noqa: E731
        total = val(kernel) + val(user)  # kernel time includes idle time
        return total - val(idle), total
    return None


def cpu_busy(interval: float = 0.5) -> float | None:
    """System-wide CPU use in percent over ``interval`` seconds (None where unsupported)."""
    a = _cpu_times()
    if a is None:
        if hasattr(os, "getloadavg"):
            return min(100.0, 100.0 * os.getloadavg()[0] / (os.cpu_count() or 1))
        return None
    time.sleep(interval)
    b = _cpu_times()
    dt = b[1] - a[1]
    return 100.0 * (b[0] - a[0]) / dt if dt > 0 else 0.0


def wait_until_quiet(threshold: float, max_wait: float, interval: float = 0.5, progress=None) -> float | None:
    """Sample CPU use until it is below ``threshold`` percent or ``max_wait`` seconds pass; return the last sample."""
    started, told = time.monotonic(), False
    while True:
        busy = cpu_busy(interval)
        if busy is None or busy <= threshold or time.monotonic() - started >= max_wait:
            if busy is not None and busy > threshold and progress:
                progress(f"warning: machine still {busy:.0f}% busy after {max_wait:.0f} s; timing anyway")
            return busy
        if progress and not told:
            progress(f"machine {busy:.0f}% busy (> {threshold:.0f}%), waiting ...")
            told = True

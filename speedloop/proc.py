"""Start measured child processes: CPU pinning, peak memory, timeouts and parallel copies.

Peak memory (``peak_rss_mib``):

* Linux / macOS: from ``wait4`` resource usage, which covers the child and every descendant it waited
  for (so ``python -m tool`` that spawns workers is counted).
* Windows: the child's peak working set (``GetProcessMemoryInfo``). This is the direct child only, so
  launch the real program, not a launcher (``python script.py``, not ``py script.py``).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

METRIC_LINE = re.compile(r"^SPEEDLOOP_METRIC\s+([A-Za-z0-9_.:/-]+)=([-+0-9.eE]+)\s*$")
THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
               "NUMBA_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


def parse_metrics(stdout: str) -> dict[str, float]:
    out = {}
    for line in stdout.splitlines():
        m = METRIC_LINE.match(line.strip())
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


class CommandFailed(RuntimeError):
    pass


# --- CPU pinning ---------------------------------------------------------------------------------------

class CpuLimit:
    """Run children on ``cpus`` cores with thread pools sized to match.

    * Linux: the child pins itself to the first ``cpus`` allowed cores before exec (``sched_setaffinity``).
    * Windows: affinity masks are inherited, so this process narrows its own mask while it starts the
      child, then restores it; the child never runs unpinned. (One processor group: up to 64 logical CPUs.)
    * macOS: there is no affinity API; only the thread-pool variables apply, with a warning.
    """

    def __init__(self, cpus: int | None):
        self.cpus = cpus
        self.env = dict(os.environ, **{v: str(cpus) for v in THREAD_VARS}) if cpus else None
        self.preexec = None
        self._win_mask = None
        if not cpus:
            return
        if hasattr(os, "sched_setaffinity"):
            cores = sorted(os.sched_getaffinity(0))
            self._check(len(cores))
            chosen = set(cores[:cpus])
            self.preexec = lambda: os.sched_setaffinity(0, chosen)
        elif sys.platform == "win32":
            current = win_affinity()
            bits = [b for b in range(64) if current >> b & 1]
            self._check(len(bits))
            self._win_mask = sum(1 << b for b in bits[:cpus])
        else:
            import warnings
            warnings.warn("no CPU affinity on this platform: only thread-pool variables are capped", stacklevel=2)

    def _check(self, available: int) -> None:
        if self.cpus > available:
            raise ValueError(f"asked for {self.cpus} CPUs, this process may use {available}")

    @contextmanager
    def spawning(self):
        if self._win_mask is None:
            yield
            return
        previous = win_affinity()
        win_set_affinity(self._win_mask)
        try:
            yield
        finally:
            win_set_affinity(previous)


def _k32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    return ctypes, wintypes, k32


def win_affinity() -> int:
    ctypes, wintypes, k32 = _k32()
    proc_mask, sys_mask = ctypes.c_size_t(), ctypes.c_size_t()
    if not k32.GetProcessAffinityMask(wintypes.HANDLE(k32.GetCurrentProcess()), ctypes.byref(proc_mask),
                                      ctypes.byref(sys_mask)):
        raise ctypes.WinError(ctypes.get_last_error())
    return proc_mask.value


def win_set_affinity(mask: int) -> None:
    ctypes, wintypes, k32 = _k32()
    if not k32.SetProcessAffinityMask(wintypes.HANDLE(k32.GetCurrentProcess()), ctypes.c_size_t(mask)):
        raise ctypes.WinError(ctypes.get_last_error())


def _win_peak_mib(handle) -> float | None:
    ctypes, wintypes, k32 = _k32()

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    c = Counters()
    c.cb = ctypes.sizeof(c)
    if not k32.K32GetProcessMemoryInfo(wintypes.HANDLE(int(handle)), ctypes.byref(c), c.cb):
        return None
    return c.PeakWorkingSetSize / 2**20


# --- running ---------------------------------------------------------------------------------------------

@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    wall_s: float
    peak_rss_mib: float | None
    metrics: dict = field(default_factory=dict)


def spawn_and_wait(argv: list[str], cwd: str | None = None, timeout: float | None = None,
                   limit: CpuLimit | None = None) -> Result:
    limit = limit or CpuLimit(None)
    started = time.perf_counter()
    with limit.spawning():
        proc = subprocess.Popen(argv, cwd=cwd, env=limit.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", preexec_fn=limit.preexec)
    out, err = [], []
    readers = [threading.Thread(target=lambda s=s, b=b: b.append(s.read()), daemon=True)
               for s, b in ((proc.stdout, out), (proc.stderr, err))]
    for t in readers:
        t.start()
    timed_out = threading.Event()

    def kill_on_timeout():
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, kill_on_timeout) if timeout else None
    if timer:
        timer.start()
    peak = None
    try:
        if hasattr(os, "wait4"):
            _, status, usage = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
            # ru_maxrss: KiB on Linux, bytes on macOS
            peak = usage.ru_maxrss / (2**20 if sys.platform == "darwin" else 2**10)
        else:
            proc.wait()
            if sys.platform == "win32":
                peak = _win_peak_mib(proc._handle)
    finally:
        if timer:
            timer.cancel()
    wall = time.perf_counter() - started
    for t in readers:
        t.join()
    proc.stdout.close()
    proc.stderr.close()
    if timed_out.is_set():
        raise subprocess.TimeoutExpired(argv, timeout)
    return Result(proc.returncode, "".join(out), "".join(err), wall, peak)


def measure(argv: list[str], cwd: str | None = None, timeout: float | None = None, limit: CpuLimit | None = None,
            parallel: int = 1) -> dict[str, float]:
    """Run ``parallel`` copies at once; return the metrics of one run.

    Metrics: ``wall_s``, ``peak_rss_mib`` and everything the command printed. With several copies each
    metric is the worst (largest) across copies, and ``peak_rss_mib_total`` is the sum of their peaks.
    """
    results: list[Result | BaseException] = [None] * parallel  # type: ignore[list-item]

    def one(i):
        try:
            results[i] = spawn_and_wait(argv, cwd, timeout, limit)
        except BaseException as e:  # noqa: BLE001 - re-raised below
            results[i] = e

    threads = [threading.Thread(target=one, args=(i,)) for i in range(parallel)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    metrics: dict[str, float] = {}
    peaks = []
    for r in results:
        if isinstance(r, BaseException):
            raise r
        if r.returncode != 0:
            raise CommandFailed(f"command failed ({r.returncode}): {' '.join(argv)}\n{r.stderr[-2000:]}")
        own = parse_metrics(r.stdout)
        own.setdefault("wall_s", r.wall_s)
        if r.peak_rss_mib is not None:
            own.setdefault("peak_rss_mib", r.peak_rss_mib)
            peaks.append(r.peak_rss_mib)
        for k, v in own.items():
            metrics[k] = max(metrics.get(k, v), v)
    if parallel > 1 and peaks:
        metrics["peak_rss_mib_total"] = sum(peaks)
    return metrics

"""Interleaved A/B timing with an A/A noise floor.

Runs the base command (A) and the candidate (B) in ABBA order, so slow drift of the machine (thermal,
background load, caches) hits both sides equally, plus A/A pairs (A against itself) to measure how much
the same code varies run to run.

Each run is a subprocess. Its wall time is always recorded as the metric ``wall_s``. A command can report
its own metrics by printing lines such as::

    SPEEDLOOP_METRIC step_one=0.412
    SPEEDLOOP_METRIC peak_mib=812

Every metric is compared with the same noise rule (``speedloop.noise``).
"""
from __future__ import annotations

import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import noise

METRIC_LINE = re.compile(r"^SPEEDLOOP_METRIC\s+([A-Za-z0-9_.:/-]+)=([-+0-9.eE]+)\s*$")


def schedule(pairs: int, aa_pairs: int) -> list[str]:
    """Run order: A/A pairs first (A, A2, A2, A), then ABBA pairs."""
    order = []
    for _ in range(aa_pairs):
        order += ["A", "A2", "A2", "A"]
    for _ in range(pairs):
        order += ["A", "B", "B", "A"]
    return order


def parse_metrics(stdout: str) -> dict[str, float]:
    out = {}
    for line in stdout.splitlines():
        m = METRIC_LINE.match(line.strip())
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
               "NUMBA_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


class CpuLimit:
    """Run children on ``cpus`` cores with thread pools sized to match.

    * Linux: the child pins itself to the first ``cpus`` allowed cores before exec (``sched_setaffinity``).
    * Windows: affinity masks are inherited, so this process narrows its own mask while it starts the
      child, then restores it. The child never runs unpinned. (Processes limited to one processor
      group, i.e. up to 64 logical CPUs.)
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
            current = _win_affinity()
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
        previous = _win_affinity()
        _win_set_affinity(self._win_mask)
        try:
            yield
        finally:
            _win_set_affinity(previous)


def _win_affinity() -> int:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    proc_mask, sys_mask = ctypes.c_size_t(), ctypes.c_size_t()
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    if not k32.GetProcessAffinityMask(wintypes.HANDLE(k32.GetCurrentProcess()), ctypes.byref(proc_mask),
                                      ctypes.byref(sys_mask)):
        raise ctypes.WinError(ctypes.get_last_error())
    return proc_mask.value


def _win_set_affinity(mask: int) -> None:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    if not k32.SetProcessAffinityMask(wintypes.HANDLE(k32.GetCurrentProcess()), ctypes.c_size_t(mask)):
        raise ctypes.WinError(ctypes.get_last_error())


def run_once(argv: list[str], cwd: str | None, timeout: float | None, limit: CpuLimit | None = None) -> dict:
    limit = limit or CpuLimit(None)
    started = time.perf_counter()
    with limit.spawning():
        proc = subprocess.Popen(argv, cwd=cwd, env=limit.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, preexec_fn=limit.preexec)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    wall = time.perf_counter() - started
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {shlex.join(argv)}\n{stderr[-2000:]}")
    metrics = parse_metrics(stdout)
    metrics.setdefault("wall_s", wall)
    return metrics


def environment() -> dict:
    info = {"python": sys.version.split()[0], "platform": platform.platform(), "cpus": os.cpu_count(),
            "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        info["git"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                     timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        info["git"] = None
    return info


def run(base: list[str], cand: list[str], pairs: int = 2, aa_pairs: int = 1, warmup: int = 1,
        cwd: str | None = None, timeout: float | None = None, progress=print, cpus: int | None = None) -> dict:
    """Run the schedule and return a receipt (JSON-serialisable). ``cpus`` pins every run to that many cores."""
    commands = {"A": base, "A2": base, "B": cand}
    limit = CpuLimit(cpus)
    for _ in range(warmup):
        for label in ("A", "B"):
            run_once(commands[label], cwd, timeout, limit)
    runs = []
    order = schedule(pairs, aa_pairs)
    for i, label in enumerate(order, 1):
        metrics = run_once(commands[label], cwd, timeout, limit)
        runs.append({"arm": label, "metrics": metrics})
        progress(f"[{i}/{len(order)}] {label} wall_s={metrics['wall_s']:.3f}")
    return {"tool": "speedloop.bench", "env": environment(), "cpus": cpus, "base": base, "candidate": cand,
            "pairs": pairs, "aa_pairs": aa_pairs, "warmup": warmup, "runs": runs}


def arms(receipt: dict) -> dict[str, dict[str, list[float]]]:
    """-> {arm: {metric: [values]}}"""
    out: dict[str, dict[str, list[float]]] = {}
    for r in receipt["runs"]:
        for metric, value in r["metrics"].items():
            out.setdefault(r["arm"], {}).setdefault(metric, []).append(value)
    return out


def report(receipt: dict, higher_is_better: frozenset = frozenset()) -> tuple[str, list[noise.Verdict]]:
    """Markdown table: metric | A/A spread | base | candidate | delta | verdict."""
    a = arms(receipt)
    base, cand, aa = a.get("A", {}), a.get("B", {}), a.get("A2", {})
    lines = ["| metric | A/A spread | base median | candidate median | delta | noise | verdict |",
             "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    verdicts = []
    for metric in sorted(set(base) & set(cand)):
        # A/A spread: how far the same code ranges across every base-side run (A and its A/A twin).
        aa_spread = noise.spread(base[metric] + aa[metric]) if metric in aa else None
        v = noise.compare(base[metric], cand[metric], aa_spread, lower_is_better=metric not in higher_is_better)
        verdicts.append(v)
        aa_txt = "" if aa_spread is None else f"{aa_spread:.4g}"
        lines.append(f"| {metric} | {aa_txt} | {v.base_median:.4g} | {v.cand_median:.4g} | {v.delta:+.4g} | "
                     f"{v.noise:.4g} | {v.verdict} |")
    return "\n".join(lines), verdicts


def save(receipt: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=1), encoding="utf-8")

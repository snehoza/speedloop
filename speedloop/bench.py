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


def cpu_limit(cpus: int | None) -> tuple[dict | None, object]:
    """(env, preexec_fn) that pin a child to the first ``cpus`` cores and size thread pools to match."""
    if not cpus:
        return None, None
    if not hasattr(os, "sched_setaffinity"):
        raise OSError("--cpus needs Linux (os.sched_setaffinity); run inside Linux or WSL")
    cores = sorted(os.sched_getaffinity(0))
    if cpus > len(cores):
        raise ValueError(f"asked for {cpus} CPUs, this process may use {len(cores)}")
    chosen = set(cores[:cpus])
    env = dict(os.environ, **{v: str(cpus) for v in THREAD_VARS})
    return env, lambda: os.sched_setaffinity(0, chosen)


def run_once(argv: list[str], cwd: str | None, env: dict | None, timeout: float | None, preexec=None) -> dict:
    started = time.perf_counter()
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                          preexec_fn=preexec)
    wall = time.perf_counter() - started
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {shlex.join(argv)}\n{proc.stderr[-2000:]}")
    metrics = parse_metrics(proc.stdout)
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
    env, preexec = cpu_limit(cpus)
    for _ in range(warmup):
        for label in ("A", "B"):
            run_once(commands[label], cwd, env, timeout, preexec)
    runs = []
    order = schedule(pairs, aa_pairs)
    for i, label in enumerate(order, 1):
        metrics = run_once(commands[label], cwd, env, timeout, preexec)
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

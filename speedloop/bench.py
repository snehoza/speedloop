"""Interleaved A/B timing with an A/A noise floor.

Runs the base command (A) and the candidate (B) in ABBA order, so slow drift of the machine (thermal,
background load, caches) hits both sides equally, plus A/A pairs (A against itself) to measure how much the
same code varies run to run.

Each run is a subprocess (``speedloop.proc``). Every run records ``wall_s`` and ``peak_rss_mib``; a command
can add its own metrics by printing lines such as::

    SPEEDLOOP_METRIC step_one=0.412

Every metric is judged with the same noise rule (``speedloop.noise``). The whole schedule holds the
heavy-job lock, and each run waits for the machine to be quiet first (``speedloop.machine``).
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import machine, noise
from .proc import CpuLimit, measure, parse_metrics  # noqa: F401  (parse_metrics re-exported)


def schedule(pairs: int, aa_pairs: int) -> list[str]:
    """Run order: A/A pairs first (A, A2, A2, A), then ABBA pairs."""
    order = []
    for _ in range(aa_pairs):
        order += ["A", "A2", "A2", "A"]
    for _ in range(pairs):
        order += ["A", "B", "B", "A"]
    return order


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
        cwd: str | None = None, timeout: float | None = None, progress=print, cpus: int | None = None,
        parallel: int = 1, lock: bool | Path = True, lock_timeout: float | None = None,
        quiet_threshold: float | None = 25.0, quiet_wait: float = 60.0) -> dict:
    """Run the schedule and return a receipt (JSON-serialisable).

    ``cpus`` pins every run to that many cores; ``parallel`` runs that many copies of each command at once
    (a load check). ``lock`` holds the heavy-job lock (True = default path, a Path = that file, False =
    none). Before each run the machine must drop below ``quiet_threshold`` % CPU, waiting up to
    ``quiet_wait`` seconds in total across the schedule (None disables the check): on a machine that never
    gets quiet, the first runs use up the budget and the rest start at once, flagged in the receipt.
    """
    commands = {"A": base, "A2": base, "B": cand}
    limit = CpuLimit(cpus)
    held = machine.HeavyLock(lock if isinstance(lock, Path) else None, lock_timeout, progress=progress) if lock else None
    if held:
        held.acquire()
    try:
        for _ in range(warmup):
            for label in ("A", "B"):
                measure(commands[label], cwd, timeout, limit, parallel)
        runs = []
        order = schedule(pairs, aa_pairs)
        wait_left = quiet_wait
        for i, label in enumerate(order, 1):
            busy = None
            if quiet_threshold is not None:
                started = time.monotonic()
                busy = machine.wait_until_quiet(quiet_threshold, wait_left,
                                                progress=progress if wait_left > 0 else None)
                wait_left = max(0.0, wait_left - (time.monotonic() - started))
            metrics = measure(commands[label], cwd, timeout, limit, parallel)
            runs.append({"arm": label, "busy_pct_before": busy, "metrics": metrics})
            progress(f"[{i}/{len(order)}] {label} wall_s={metrics['wall_s']:.3f}")
    finally:
        if held:
            held.release()
    return {"tool": "speedloop.bench", "env": environment(), "cpus": cpus, "parallel": parallel,
            "base": base, "candidate": cand, "pairs": pairs, "aa_pairs": aa_pairs, "warmup": warmup,
            "quiet_threshold": quiet_threshold, "runs": runs}


def arms(receipt: dict) -> dict[str, dict[str, list[float]]]:
    """-> {arm: {metric: [values]}}"""
    out: dict[str, dict[str, list[float]]] = {}
    for r in receipt["runs"]:
        for metric, value in r["metrics"].items():
            out.setdefault(r["arm"], {}).setdefault(metric, []).append(value)
    return out


def _table(base: dict, cand: dict, aa: dict, higher_is_better) -> tuple[list[str], list[noise.Verdict]]:
    lines = ["| metric | A/A spread | base median | candidate median | delta | delta % | noise | verdict |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    verdicts = []
    for metric in sorted(set(base) & set(cand)):
        # A/A spread: how far the same code ranges across every base-side run (A and its A/A twin).
        aa_spread = noise.spread(base[metric] + aa[metric]) if metric in aa else None
        v = noise.compare(base[metric], cand[metric], aa_spread, lower_is_better=metric not in higher_is_better)
        verdicts.append(v)
        aa_txt = "" if aa_spread is None else f"{aa_spread:.4g}"
        pct = f"{100 * v.delta / v.base_median:+.1f}%" if v.base_median else ""
        lines.append(f"| {metric} | {aa_txt} | {v.base_median:.4g} | {v.cand_median:.4g} | {v.delta:+.4g} | "
                     f"{pct} | {v.noise:.4g} | {v.verdict} |")
    return lines, verdicts


def report(receipt: dict, higher_is_better: frozenset = frozenset()) -> tuple[str, list[noise.Verdict]]:
    """Markdown table: metric | A/A spread | base | candidate | delta | noise | verdict."""
    a = arms(receipt)
    lines, verdicts = _table(a.get("A", {}), a.get("B", {}), a.get("A2", {}), higher_is_better)
    busy = [r["busy_pct_before"] for r in receipt["runs"] if r.get("busy_pct_before") is not None]
    if busy and max(busy) > (receipt.get("quiet_threshold") or 100):
        lines.append(f"\nWarning: the machine was up to {max(busy):.0f}% busy before some runs.")
    return "\n".join(lines), verdicts


def compare_receipts(base_receipt: dict, cand_receipt: dict, base_arm: str = "A", cand_arm: str = "B",
                     higher_is_better: frozenset = frozenset()) -> tuple[str, list[noise.Verdict]]:
    """Compare arms from two different receipts (e.g. reuse last round's base for a new candidate).

    The runs were not interleaved, so machine drift between the two sessions is not cancelled: treat the
    verdict as a screen, and confirm a result you keep with an interleaved ``bench``.
    """
    ba, ca = arms(base_receipt), arms(cand_receipt)
    aa = ba.get("A2", {}) if base_arm == "A" else {}
    lines, verdicts = _table(ba.get(base_arm, {}), ca.get(cand_arm, {}), aa, higher_is_better)
    warn = ["", "Cross-receipt comparison: the two sides were not interleaved, so drift between sessions is not "
            "cancelled. Use it to screen; confirm with an interleaved bench before claiming a gain."]
    for key in ("cpus", "parallel"):
        if base_receipt.get(key) != cand_receipt.get(key):
            warn.append(f"Warning: {key} differs ({base_receipt.get(key)} vs {cand_receipt.get(key)}).")
    if base_receipt.get("env", {}).get("platform") != cand_receipt.get("env", {}).get("platform"):
        warn.append("Warning: the receipts come from different platforms.")
    return "\n".join(lines + warn), verdicts


def save(receipt: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=1), encoding="utf-8")

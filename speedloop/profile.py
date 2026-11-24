"""Profile a Python program and print where the time goes, so a round starts from data, not guesses.

    speedloop profile script.py --arg 1
    speedloop profile -m package.module --arg 1

Runs the program under cProfile in a child process, saves the ``.prof`` file (open it in snakeviz or
similar) and prints the top functions by cumulative and by own time.
"""
from __future__ import annotations

import io
import pstats
import subprocess
import sys
from pathlib import Path


def run(target: list[str], out: Path, top: int = 25) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-m", "cProfile", "-o", str(out), *target]
    proc = subprocess.run(argv)
    if proc.returncode != 0:
        raise RuntimeError(f"profiled program exited with {proc.returncode}")
    return summary(out, top)


def summary(prof: Path, top: int = 25) -> str:
    parts = []
    for key, title in (("cumulative", "by cumulative time (includes callees)"), ("tottime", "by own time")):
        buf = io.StringIO()
        stats = pstats.Stats(str(prof), stream=buf)
        stats.strip_dirs().sort_stats(key).print_stats(top)
        parts.append(f"## Top {top} {title}\n\n```\n{buf.getvalue().strip()}\n```")
    return "\n\n".join(parts)

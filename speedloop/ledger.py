"""Span ledger: a time tree per action, with run-to-run noise and a noise-aware delta against a base.

Input is a JSON trace file (any number per side; each file may hold several runs)::

    {"runs": [
      {"action": "load_page",                 # what the user did (one tree per action)
       "start": 0.0, "end": 1.84,             # the action window, seconds on one clock
       "spans": [{"name": "fetch", "start": 0.01, "end": 0.9, "thread": "main"}, ...]}
    ]}

Spans nest by time containment. Spans on the action's main thread (``--main-thread``, default: the thread
of the longest span) make the tree; spans on other threads are listed as "beside" work, because they
overlap the action rather than add to its wall time. "(unspanned)" is wall time no top-level span covers.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from . import noise

TOL = 0.0015  # seconds: span end stamps are usually taken just after the timed block


def _tree(spans):
    spans = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    roots, stack = [], []
    for start, end, name in spans:
        node = {"name": name, "start": start, "end": end, "children": []}
        while stack and not (start >= stack[-1]["start"] - TOL and end <= stack[-1]["end"] + TOL):
            stack.pop()
        (stack[-1]["children"] if stack else roots).append(node)
        stack.append(node)
    return roots


def _flatten(nodes, prefix, out):
    for node in nodes:
        path = prefix + (node["name"],)
        inc = (node["end"] - node["start"]) * 1000.0
        child = sum((c["end"] - c["start"]) * 1000.0 for c in node["children"])
        row = out.setdefault(path, [0.0, 0.0, 0])
        row[0] += inc
        row[1] += max(0.0, inc - child)
        row[2] += 1
        _flatten(node["children"], path, out)


def _union_ms(intervals):
    total, end = 0.0, None
    for s, e in sorted(intervals):
        if end is None or s > end:
            total, end = total + e - s, e
        elif e > end:
            total, end = total + e - end, e
    return total * 1000.0


def breakdown(run: dict, main_thread: str | None = None):
    """One run -> (wall_ms, {path: [inclusive_ms, self_ms, calls]})."""
    a, b = run["start"], run["end"]
    spans = [s for s in run.get("spans", []) if s["end"] >= a - TOL and s["start"] <= b + TOL and s["end"] > s["start"]]
    if main_thread is None and spans:
        main_thread = max(spans, key=lambda s: s["end"] - s["start"]).get("thread", "main")
    clip = lambda s: (max(s["start"], a), min(s["end"], b), s["name"])  # noqa: E731
    on_main = [clip(s) for s in spans if s.get("thread", "main") == main_thread]
    rows: dict = {}
    roots = _tree(on_main)
    _flatten(roots, (), rows)
    by_thread = defaultdict(list)
    for s in spans:
        if s.get("thread", "main") != main_thread:
            by_thread[s.get("thread", "?")].append(clip(s))
    for thread, items in by_thread.items():
        _flatten(_tree(items), (f"[beside: {thread}]",), rows)
    wall = (b - a) * 1000.0
    gap = max(0.0, wall - _union_ms([(r["start"], r["end"]) for r in roots]))
    rows[("(unspanned)",)] = [gap, gap, 1]
    return wall, rows


def _main_thread(runs) -> str | None:
    """The thread that holds the most span time across all runs of one action."""
    totals: dict = defaultdict(float)
    for run in runs:
        for s in run.get("spans", []):
            totals[s.get("thread", "main")] += max(0.0, min(s["end"], run["end"]) - max(s["start"], run["start"]))
    return max(totals, key=totals.get) if totals else None


def load(paths, main_thread=None):
    """-> {action: [(wall_ms, rows), ... one per run]}

    Without ``main_thread`` the thread with the most span time across all runs of an action is used,
    the same for every run, so the trees are comparable.
    """
    by_action = defaultdict(list)
    for path in paths:
        for run in json.loads(Path(path).read_text(encoding="utf-8"))["runs"]:
            by_action[run["action"]].append(run)
    out = defaultdict(list)
    for action, runs in by_action.items():
        thread = main_thread or _main_thread(runs)
        out[action] = [breakdown(run, thread) for run in runs]
    return out


def render(cand, base=None, aa=None, min_ms: float = 5.0) -> str:
    base, aa = base or {}, aa or {}
    lines = ["# Speed ledger", "",
             "Times in ms, median over runs; range = min-max across runs. Delta = candidate - base. A delta counts "
             "only beyond the run-to-run noise (and the A/A spread) with the two sides fully separated.", ""]
    for action, runs in cand.items():
        walls = [w for w, _ in runs]
        bruns = base.get(action, [])
        bwalls = [w for w, _ in bruns]
        aa_walls = [w for w, _ in aa.get(action, [])]
        aa_spread = noise.spread(aa_walls) if len(aa_walls) > 1 else None
        head = (f"## {action}: {statistics.median(walls):.0f} ms "
                f"(range {min(walls):.0f}-{max(walls):.0f}, n={len(walls)})")
        if bwalls:
            v = noise.compare(bwalls, walls, aa_spread)
            head += f" · base {v.base_median:.0f} ms (n={len(bwalls)}) · delta {v.delta:+.0f} -> {v.verdict}"
        if aa_spread is not None:
            head += f" · A/A spread {aa_spread:.0f}"
        lines += [head, "", "| span | incl | self | calls | range | base incl | delta | verdict |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
        paths = set().union(*(rows for _, rows in runs))
        col = lambda rowsets, p, i: [rows.get(p, [0.0, 0.0, 0])[i] for _, rows in rowsets]  # noqa: E731
        ordered = sorted(paths, key=lambda p: (p[0].startswith("[beside"), p))
        for p in ordered:
            inc = col(runs, p, 0)
            if statistics.median(inc) < min_ms and p != ("(unspanned)",):
                continue
            label = "&nbsp;&nbsp;" * (len(p) - 1) + p[-1]
            cells = [label, f"{statistics.median(inc):.0f}", f"{statistics.median(col(runs, p, 1)):.0f}",
                     f"{statistics.median(col(runs, p, 2)):g}", f"{min(inc):.0f}-{max(inc):.0f}"]
            if bruns and not any(p in rows for _, rows in bruns):
                cells += ["", "", "new"]
            elif bruns:
                v = noise.compare(col(bruns, p, 0), inc)
                cells += [f"{v.base_median:.0f}", f"{v.delta:+.0f}", v.verdict]
            else:
                cells += ["", "", ""]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)

"""Cache registry: every cache the code keeps is documented, and every documented cache still exists.

Configure it in ``pyproject.toml``::

    [tool.speedloop.registry]
    doc = "docs/CACHES.md"                       # a markdown table, one row per cache
    code = ["src/**/*.py"]                       # files to scan
    pattern = "['\\"](cache_[a-z0-9_]+)['\\"]"    # regex; group 1 is the cache name as it appears in code
    row = "^\\| `([A-Za-z0-9_.]+)` \\|"           # regex for a table row's first cell

Each row should say what the cache holds, its key and invalidation, and its size. The keep rule below
decides whether a cache is worth its memory.
"""
from __future__ import annotations

import re
from pathlib import Path

KEEP_S_PER_100MIB = 1.0  # keep a cache that saves at least 1 s for every 100 MiB it holds
DROP_S_PER_100MIB = 0.5  # drop one that saves less than 0.5 s per 100 MiB; in between, a person decides


def load_config(root: Path) -> dict:
    import tomllib
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    cfg = data.get("tool", {}).get("speedloop", {}).get("registry")
    if not cfg:
        raise KeyError("no [tool.speedloop.registry] section in pyproject.toml")
    return cfg


def code_names(root: Path, globs: list[str], pattern: str) -> set[str]:
    rx = re.compile(pattern)
    found = set()
    for g in globs:
        for path in root.glob(g):
            if path.is_file():
                found |= set(rx.findall(path.read_text(encoding="utf-8", errors="replace")))
    return found


def doc_names(doc: Path, row: str) -> set[str]:
    return set(re.findall(row, doc.read_text(encoding="utf-8"), re.M))


def check(root: Path, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config(root)
    in_code = code_names(root, cfg["code"], cfg["pattern"])
    in_doc = doc_names(root / cfg["doc"], cfg["row"])
    return {"undocumented": sorted(in_code - in_doc), "stale": sorted(in_doc - in_code)}


def keep_rule(seconds_saved: float, mib_held: float) -> tuple[str, float]:
    """-> (keep | ask | drop, seconds saved per 100 MiB)."""
    if mib_held <= 0:
        return "keep", float("inf")
    rate = seconds_saved / (mib_held / 100.0)
    if rate >= KEEP_S_PER_100MIB:
        return "keep", rate
    if rate < DROP_S_PER_100MIB:
        return "drop", rate
    return "ask", rate

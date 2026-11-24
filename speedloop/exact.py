"""Exactness: prove a speed change did not change the output, for any kind of output.

``build`` snapshots a folder of outputs as a golden copy plus a SHA-256 manifest. ``check`` compares a
fresh output folder against it, file by file:

* **Same bytes:** ``identical``.
* **Different bytes:** the files are compared by content, so formatting noise is told apart from real
  changes and a rounding-level change from a meaningful one:

  | kind | files | compared |
  | --- | --- | --- |
  | array | ``.npy`` ``.npz`` (numpy); images (numpy + Pillow) | every value |
  | json | ``.json`` | structure must match; numbers compared as values |
  | table | ``.csv`` ``.tsv`` | shape must match; numeric cells compared as values, text cells must match |
  | text | other UTF-8 files | changed lines counted |
  | binary | anything else | only "differs" |

Each changed file gets a tolerance verdict:

* ``identical``: every value equal (e.g. only number formatting or metadata changed).
* ``adopt``: largest difference <= ``adopt_max``. Rounding level: count it and report it.
* ``ask``: <= ``ask_max``, or a text/binary change. A person reviews it (``review`` writes the files to
  look at) and decides.
* ``reject``: larger, or the structure changed (shape, keys, a non-numeric value).

Tolerances are absolute by default (in the output's own units: levels of an 8-bit image, metres, euros...)
or relative to the golden value with ``relative=True``. Choose them per project; the defaults (1 and 3)
suit integer-valued outputs such as images.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import json
import math
import shutil
from pathlib import Path

MANIFEST = "speedloop-golden.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
ARRAY_SUFFIXES = {".npy", ".npz"} | IMAGE_SUFFIXES
TABLE_SUFFIXES = {".csv": ",", ".tsv": "\t"}

ADOPT_MAX = 1.0   # default: largest difference nobody needs to look at
ASK_MAX = 3.0     # default: up to this a person decides; above it, reject
ORDER = ("identical", "adopt", "ask", "reject")
_TINY = 1e-12


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(root: Path):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != MANIFEST)


def build(outputs: Path, golden: Path) -> dict:
    if golden.exists():
        raise FileExistsError(f"{golden} exists; goldens are never overwritten, pick a new folder")
    shutil.copytree(outputs, golden)
    manifest = {"files": {rel: sha256(golden / rel) for rel in _files(golden)}}
    (golden / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


# --- content comparison ---------------------------------------------------------------------------------

class _Acc:
    """Accumulates value differences across a file."""

    def __init__(self):
        self.total = self.changed = self.non_numeric = 0
        self.max_abs = self.max_rel = 0.0
        self.structure = False

    def number(self, a: float, b: float) -> None:
        self.total += 1
        if a == b or (isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b)):
            return
        self.changed += 1
        d = abs(a - b)
        self.max_abs = max(self.max_abs, d)
        self.max_rel = max(self.max_rel, d / max(abs(a), _TINY))

    def other(self, a, b) -> None:
        self.total += 1
        if a != b:
            self.changed += 1
            self.non_numeric += 1

    def result(self, kind: str, **extra) -> dict:
        return {"kind": kind, "values": self.total, "changed": self.changed, "max_abs": self.max_abs,
                "max_rel": self.max_rel, "non_numeric_changed": self.non_numeric,
                "structure_changed": self.structure, **extra}


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _walk_json(a, b, acc: _Acc) -> None:
    if _is_num(a) and _is_num(b):
        acc.number(a, b)
    elif isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            acc.structure = True
        for k in a.keys() & b.keys():
            _walk_json(a[k], b[k], acc)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            acc.structure = True
        for x, y in zip(a, b):
            _walk_json(x, y, acc)
    elif type(a) is not type(b):
        acc.structure = True
    else:
        acc.other(a, b)


def _num(cell: str):
    try:
        return float(cell)
    except ValueError:
        return None


def _compare_table(a: Path, b: Path, delimiter: str) -> dict:
    read = lambda p: list(csv.reader(p.read_text(encoding="utf-8").splitlines(), delimiter=delimiter))  # noqa: E731
    ra, rb = read(a), read(b)
    acc = _Acc()
    if len(ra) != len(rb) or any(len(x) != len(y) for x, y in zip(ra, rb)):
        acc.structure = True
    for row_a, row_b in zip(ra, rb):
        for ca, cb in zip(row_a, row_b):
            na, nb = _num(ca), _num(cb)
            if na is not None and nb is not None:
                acc.number(na, nb)
            else:
                acc.other(ca, cb)
    return acc.result("table")


def _arrays(path: Path):
    import numpy as np
    suffix = path.suffix.lower()
    if suffix == ".npy":
        return {"": np.load(path)}
    if suffix == ".npz":
        with np.load(path) as z:
            return {k: z[k] for k in z.files}
    from PIL import Image
    with Image.open(path) as im:
        return {"": np.asarray(im)}


def _compare_arrays(a: Path, b: Path) -> dict | None:
    try:
        import numpy as np
        xa, xb = _arrays(a), _arrays(b)
    except ImportError:
        return None
    acc = _Acc()
    p999 = 0.0
    for key in set(xa) | set(xb):
        if key not in xa or key not in xb or xa[key].shape != xb[key].shape:
            acc.structure = True
            continue
        fa, fb = xa[key].astype(np.float64), xb[key].astype(np.float64)
        d = np.where(np.isnan(fa) & np.isnan(fb), 0.0, np.abs(fa - fb))
        acc.total += d.size
        acc.changed += int((d > 0).sum())
        if d.size:
            acc.max_abs = max(acc.max_abs, float(d.max()))
            acc.max_rel = max(acc.max_rel, float((d / np.maximum(np.abs(fa), _TINY)).max()))
            p999 = max(p999, float(np.quantile(d, 0.999)))
    return acc.result("array", p99_9_abs=p999)


def _compare_text(a: Path, b: Path) -> dict | None:
    try:
        la = a.read_text(encoding="utf-8").splitlines()
        lb = b.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in
                  difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes() if tag != "equal")
    return {"kind": "text", "lines": max(len(la), len(lb)), "lines_changed": changed}


def compare_files(golden_file: Path, new_file: Path) -> dict:
    """Content comparison of two versions of one output file (see the module docstring for the kinds)."""
    suffix = new_file.suffix.lower()
    if suffix in ARRAY_SUFFIXES:
        result = _compare_arrays(golden_file, new_file)
        if result is not None:
            return result
    if suffix == ".json":
        try:
            ja = json.loads(golden_file.read_text(encoding="utf-8"))
            jb = json.loads(new_file.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        else:
            acc = _Acc()
            _walk_json(ja, jb, acc)
            return acc.result("json")
    if suffix in TABLE_SUFFIXES:
        try:
            return _compare_table(golden_file, new_file, TABLE_SUFFIXES[suffix])
        except UnicodeDecodeError:
            pass
    return _compare_text(golden_file, new_file) or {"kind": "binary"}


def tolerance(result: dict, adopt_max: float = ADOPT_MAX, ask_max: float = ASK_MAX, relative: bool = False) -> str:
    """identical | adopt | ask | reject for one ``compare_files`` result."""
    if result["kind"] in ("text", "binary"):
        return "ask"
    if result["structure_changed"] or result["non_numeric_changed"]:
        return "reject"
    if result["changed"] == 0:
        return "identical"
    worst = result["max_rel"] if relative else result["max_abs"]
    return "adopt" if worst <= adopt_max else "ask" if worst <= ask_max else "reject"


def check(outputs: Path, golden: Path, adopt_max: float = ADOPT_MAX, ask_max: float = ASK_MAX,
          relative: bool = False) -> dict:
    """-> {"identical": bool (all bytes equal), "verdict": worst file verdict, "files": {rel: result}}

    Missing files reject; new files ask.
    """
    manifest = json.loads((golden / MANIFEST).read_text(encoding="utf-8"))["files"]
    current = set(_files(outputs))
    results = {}
    for rel, digest in manifest.items():
        if rel not in current:
            results[rel] = {"status": "missing", "verdict": "reject"}
        elif sha256(outputs / rel) == digest:
            results[rel] = {"status": "identical", "verdict": "identical"}
        else:
            diff = compare_files(golden / rel, outputs / rel)
            results[rel] = {"status": "differs", "verdict": tolerance(diff, adopt_max, ask_max, relative),
                            "diff": diff}
    for rel in sorted(current - set(manifest)):
        results[rel] = {"status": "new", "verdict": "ask"}
    verdict = max((r["verdict"] for r in results.values()), key=ORDER.index, default="identical")
    return {"identical": all(r["status"] == "identical" for r in results.values()), "verdict": verdict,
            "files": results}


# --- review files ----------------------------------------------------------------------------------------

def review(golden_file: Path, new_file: Path, out_dir: Path, amplify: float | None = None) -> Path | None:
    """Write something a person can judge for one changed output; returns its path (None if nothing applies).

    * Images: a full-resolution ``golden | new | difference`` sheet (needs numpy + Pillow). Judge it at full
      size; small changes are invisible in thumbnails.
    * Text, JSON, CSV and other UTF-8 files: a unified diff.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = new_file.name
    if new_file.suffix.lower() in IMAGE_SUFFIXES:
        return _image_sheet(golden_file, new_file, out_dir / f"{stem}.review.png", amplify)
    try:
        a = golden_file.read_text(encoding="utf-8").splitlines(keepends=True)
        b = new_file.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None
    out = out_dir / f"{stem}.review.diff"
    out.write_text("".join(difflib.unified_diff(a, b, f"golden/{stem}", f"new/{stem}")), encoding="utf-8")
    return out


def _image_sheet(golden_file: Path, new_file: Path, out: Path, amplify: float | None) -> Path:
    import numpy as np
    from PIL import Image
    a, b = _arrays(golden_file)[""], _arrays(new_file)[""]
    if a.shape != b.shape:
        raise ValueError(f"shape differs: {a.shape} vs {b.shape}")
    as_rgb = lambda x: np.repeat(x[..., None], 3, axis=2) if x.ndim == 2 else x[..., :3]  # noqa: E731
    a8, b8 = as_rgb(a).astype(np.uint8), as_rgb(b).astype(np.uint8)
    d = np.abs(as_rgb(a).astype(np.float64) - as_rgb(b).astype(np.float64)).max(axis=2)
    scale = amplify if amplify else (255.0 / d.max() if d.max() > 0 else 0.0)
    heat = np.clip(d * scale, 0, 255).astype(np.uint8)
    gap = np.full((a8.shape[0], 8, 3), 128, np.uint8)
    Image.fromarray(np.concatenate([a8, gap, b8, gap, np.repeat(heat[..., None], 3, axis=2)], axis=1)).save(out)
    return out

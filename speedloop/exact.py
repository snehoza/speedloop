"""Exactness: prove a speed change did not change the output.

``build`` snapshots a folder of outputs as a golden copy plus a manifest of SHA-256 hashes. ``check``
compares a fresh output folder against it:

* identical bytes -> ``identical``;
* different bytes, numeric arrays (``.npy`` / ``.npz``, needs numpy; images, needs numpy + Pillow) ->
  the number of changed values, the max absolute difference and the 99.9th percentile, so a
  rounding-level change can be told apart from a real one;
* anything else -> ``differs``.

A golden must be self-contained: everything it needs lives inside its own folder.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

MANIFEST = "speedloop-golden.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


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


def _arrays(path: Path):
    import numpy as np
    suffix = path.suffix.lower()
    if suffix == ".npy":
        return {"": np.load(path)}
    if suffix == ".npz":
        with np.load(path) as z:
            return {k: z[k] for k in z.files}
    if suffix in IMAGE_SUFFIXES:
        from PIL import Image
        with Image.open(path) as im:
            return {"": np.asarray(im)}
    return None


def numeric_diff(a: Path, b: Path) -> dict | None:
    """Per-array difference stats, or None when the files are not numeric or a library is missing."""
    try:
        import numpy as np
        xa, xb = _arrays(a), _arrays(b)
    except ImportError:
        return None
    if xa is None or xb is None:
        return None
    out = {}
    for key in sorted(set(xa) | set(xb)):
        if key not in xa or key not in xb or xa[key].shape != xb[key].shape:
            out[key or "array"] = {"shape_mismatch": True}
            continue
        d = np.abs(xa[key].astype(np.float64) - xb[key].astype(np.float64))
        changed = d > 0
        out[key or "array"] = {"changed": int(changed.sum()), "of": int(d.size), "max_abs": float(d.max(initial=0)),
                               "p99_9_abs": float(np.quantile(d, 0.999)) if d.size else 0.0}
    return out


def check(outputs: Path, golden: Path) -> dict:
    """-> {"identical": bool, "files": {rel: result}}"""
    manifest = json.loads((golden / MANIFEST).read_text(encoding="utf-8"))["files"]
    current = set(_files(outputs))
    results = {}
    for rel, digest in manifest.items():
        if rel not in current:
            results[rel] = {"status": "missing"}
        elif sha256(outputs / rel) == digest:
            results[rel] = {"status": "identical"}
        else:
            stats = numeric_diff(golden / rel, outputs / rel)
            results[rel] = {"status": "differs", **({"numeric": stats} if stats else {})}
    for rel in sorted(current - set(manifest)):
        results[rel] = {"status": "new"}
    return {"identical": all(r["status"] == "identical" for r in results.values()), "files": results}

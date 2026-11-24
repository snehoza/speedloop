"""In-process microbenchmark: two implementations, same input, exactness first, then interleaved timing.

    from speedloop import micro

    receipt = micro.compare(old_kernel, new_kernel, make_input=lambda: load_test_array(), pairs=5)
    print(micro.report(receipt))

Use it for kernel-level changes, where a byte-compare plus a microbenchmark is the proof and the
estimate, with no end-to-end run needed. The comparison is first checked for equality (``equal`` below
handles numpy arrays, nested lists/tuples/dicts and plain values), then timed in A/A + ABBA order with the
garbage collector paused, so the same noise rule as ``bench`` applies.
"""
from __future__ import annotations

import gc
import time

from . import bench


def equal(a, b) -> bool:
    try:
        import numpy as np
        if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
            return (isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and a.shape == b.shape
                    and a.dtype == b.dtype and bool(np.array_equal(a, b, equal_nan=a.dtype.kind in "fc")))
    except ImportError:
        pass
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return type(a) is type(b) and len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return a == b


def max_abs_diff(a, b) -> float | None:
    try:
        import numpy as np
        if isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and a.shape == b.shape:
            return float(np.abs(a.astype(np.float64) - b.astype(np.float64)).max(initial=0))
    except ImportError:
        pass
    return None


def _time(fn, arg, has_input: bool, inner: int) -> float:
    gc_was = gc.isenabled()
    gc.disable()
    try:
        start = time.perf_counter()
        for _ in range(inner):
            fn(arg) if has_input else fn()
        return (time.perf_counter() - start) / inner
    finally:
        if gc_was:
            gc.enable()


def compare(base, cand, make_input=None, pairs: int = 5, aa_pairs: int = 1, inner: int = 1, warmup: int = 1,
            check=equal) -> dict:
    """Return a receipt in ``bench`` format (metric ``time_s`` per call) plus an exactness result."""
    has_input = make_input is not None
    arg = make_input() if has_input else None
    call = (lambda f: f(arg)) if has_input else (lambda f: f())
    rb, rc = call(base), call(cand)
    exact = bool(check(rb, rc))
    for _ in range(warmup):
        call(base)
        call(cand)
    fns = {"A": base, "A2": base, "B": cand}
    runs = [{"arm": arm, "metrics": {"time_s": _time(fns[arm], arg, has_input, inner)}}
            for arm in bench.schedule(pairs, aa_pairs)]
    return {"tool": "speedloop.micro", "env": bench.environment(), "exact": exact,
            "max_abs_diff": None if exact else max_abs_diff(rb, rc), "inner": inner, "runs": runs}


def report(receipt: dict) -> str:
    table, _ = bench.report(receipt)
    head = "Exact: identical results." if receipt["exact"] else (
        "NOT EXACT: results differ" + (f" (max |diff| {receipt['max_abs_diff']:.4g})"
                                        if receipt.get("max_abs_diff") is not None else "") + ".")
    return head + "\n\n" + table

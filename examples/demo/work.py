"""A tiny program to try Speed Loop on: the same result two ways, one slow and one fast.

    python examples/demo/work.py --impl slow --out results-demo/slow
    python examples/demo/work.py --impl fast --out results-demo/fast
"""
import argparse
import time
from pathlib import Path


def histogram_slow(values, bins):
    counts = [0] * bins
    for v in values:
        for b in range(bins):                 # scans every bin for every value
            if b / bins <= v < (b + 1) / bins:
                counts[b] += 1
                break
    return counts


def histogram_fast(values, bins):
    counts = [0] * bins
    for v in values:
        counts[min(int(v * bins), bins - 1)] += 1   # direct index
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=["slow", "fast"], required=True)
    ap.add_argument("--n", type=int, default=200_000)
    ap.add_argument("--bins", type=int, default=64)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    values = [((i * 2654435761) % 2**32) / 2**32 for i in range(args.n)]  # deterministic pseudo-random
    started = time.perf_counter()
    counts = (histogram_slow if args.impl == "slow" else histogram_fast)(values, args.bins)
    print(f"SPEEDLOOP_METRIC histogram_s={time.perf_counter() - started:.6f}")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "counts.txt").write_text("\n".join(map(str, counts)) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

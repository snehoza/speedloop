"""Noise-aware comparison of two sets of timings.

A speed change counts only when it is bigger than the run-to-run noise. Noise is the larger of:

* the spread (max - min) of the base runs,
* the spread of the candidate runs,
* the A/A spread: the same base measured against itself, when given.

On top of that, the two sides must separate: every candidate run is faster than every base run, or
every one is slower. A median that moves while the runs still overlap is reported as noise.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

FASTER, SLOWER, NOISE, TOO_FEW = "faster", "SLOWER", "noise", "too few runs"


@dataclass(frozen=True)
class Verdict:
    base_median: float
    cand_median: float
    delta: float
    noise: float
    separated: bool
    verdict: str

    @property
    def counts(self) -> bool:
        return self.verdict in (FASTER, SLOWER)


def spread(values) -> float:
    values = list(values)
    return max(values) - min(values) if len(values) > 1 else 0.0


def compare(base, cand, aa_spread: float | None = None, lower_is_better: bool = True) -> Verdict:
    """Compare candidate runs against base runs.

    ``lower_is_better`` is True for times and memory; set it False for throughput-like metrics.
    With fewer than two runs on either side there is no noise estimate, so nothing counts.
    """
    base, cand = list(base), list(cand)
    if not base or not cand:
        raise ValueError("compare needs at least one run on each side")
    bm, cm = statistics.median(base), statistics.median(cand)
    delta = cm - bm
    noise = max(spread(base), spread(cand), aa_spread or 0.0)
    separated = max(cand) < min(base) or min(cand) > max(base)
    if len(base) < 2 or len(cand) < 2:
        verdict = TOO_FEW
    elif abs(delta) > noise and separated:
        improved = delta < 0 if lower_is_better else delta > 0
        verdict = FASTER if improved else SLOWER
    else:
        verdict = NOISE
    return Verdict(bm, cm, delta, noise, separated, verdict)

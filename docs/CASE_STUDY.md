# Case study: about 30 speed rounds on a real application

Speed Loop was built from the method used over about a month of optimisation rounds on a production image-processing web app. The app has a heavy analysis step (several vision models plus geometry) when a photo is uploaded, followed by many interactive edits that each re-render the result, and a full-resolution export. The tools here are the generalised versions of what those rounds used. This page shows what the method produced, and what it stopped.

All numbers are medians from interleaved A/A + ABBA runs on a 4-CPU laptop. Final measurements were repeated at 16 CPUs.

## What it produced

| User action | Before | After | Output |
| --- | ---: | ---: | --- |
| Photo analysis | 15.5 s | 5.9 s | identical, except one approved rounding-level change |
| First interactive preview | 5.6 s | 4.6 s | identical |
| Option switch (most common edit) | 1.8–3.1 s | 0.6 s | identical |
| Applying a new material to a surface | 12.7 s | 7.5 s | identical |
| Returning to an earlier choice | 8.6 s | 1.5–1.9 s | identical (bounded cache) |
| A second editing feature, first use | 10–13 s | 2.3–3.1 s | identical |
| Server cold start | 94–126 s | 4–12 s | rounding-level on one GPU type, approved |

Every row was checked against a golden set of 99 full-resolution renders. Each adopted change was either byte-identical, or a counted rounding-level difference that the code owner had looked at full size and approved.

## What it stopped

These are the cases the tools exist for.

**A regression that was really noise.**
- One round reported a +0.44 s and +0.72 s slowdown on an export step.
- The A/A runs showed the *unchanged* code varying by 0.8 s on the same step (10.76 s against 9.94 s).
- Under the noise rule (delta bigger than the noise, and the two sides fully separated) both "regressions" are `noise`.
- Without the A/A pairs, a good change would have been reverted.

**Caches that cost more memory than they saved.**

| Cache | Saved | Held | s per 100 MiB | Decision |
| --- | ---: | ---: | ---: | --- |
| Per-surface precomputed kit | 3.5 s on some edits | +640 MiB | 0.55 | `ask`: owner said off |
| Per-surface lighting contexts, stored densely | ~3.3 s per new choice | 514 MiB | 0.64 | `ask`: not adopted |
| The same contexts, stored sparsely (bit-identical) | ~3.3 s per new choice | 120 MiB | 2.7 | `keep` |

The last two rows show the other side of the rule. Stored densely, the cache was in the grey zone. Storing it sparsely, bit-for-bit identical, cut it from 514 to 120 MiB, and then it clearly paid for itself.

**A trade-off that only looks like a win per step.**
- Preparing an editing feature during analysis made that feature's first use 1.2–2.4 s faster.
- It also made every analysis 0.36–0.61 s slower, for every user, whether or not they used the feature.
- The ledger showed both sides, so the switch stayed off.

**Background work that looked like a gain.**
- Summing stage timings suggested several steps had sped up.
- Real end-to-end latency, measured with the background work switched on, showed some of that time had only moved onto other threads.
- That's why `ledger` lists work on other threads separately ("beside") and never adds it to the action's wall time.

**Measurements spoiled by the machine itself.**
- A test suite started by another process ran during one timing session and moved every number.
- After that, every timing job held a heavy-job lock, and everything else heavy waited its turn: `speedloop locked`, plus the quiet-machine check in `bench`.

## What changed in how the rounds were run

- **Batching.** A full measurement (A/A + two ABBA pairs, two inputs, two CPU counts, plus the ledger) cost about an hour. Rounds were therefore batched to 6–10 changes, each proven exact on its own first. One expensive measurement then covered the whole batch.
- **Testing by risk.** Exact kernel changes were proven with a byte-compare plus a microbenchmark (`speedloop.micro`), with no end-to-end run. Only threading, scheduling and memory changes paid for load and concurrency checks.
- **One queue.** Every idea went into one queue file, with its outcome and reason (`speedloop init` writes the template). Several ideas came back weeks later and were recognised as already rejected, with the numbers.

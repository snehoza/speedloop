---
name: speed-loop
description: Run a speed-optimisation round on any codebase the measured way - profile, change one thing, prove the output did not change, and keep only gains bigger than run-to-run noise. Use when asked to make code faster, run a speed round, or check whether a change really is faster.
---

# Speed loop

A round makes the code faster without changing what it produces, and every claimed gain survives a noise check. Tools: the `speedloop` package in this repo (`python -m speedloop --help`).

## Before the first change

1. **Name the targets.** Pick the user-facing actions that matter (a request, a page load, a batch job), and the metrics for each one: wall time, and peak memory when memory matters.
2. **Freeze the output.** Run the current code on a fixed set of inputs and save the outputs as a golden copy: `speedloop golden build OUTPUTS goldens/<name>`. Goldens are never overwritten; a new golden gets a new folder.
3. **Measure the noise.** Run the base against itself (A/A). `speedloop bench` does this automatically (`--aa-pairs`). A change smaller than this spread is not a result.
4. **Profile, don't guess.** Find the biggest actionable hot spot from a profile or a span ledger (`speedloop ledger`), and start there. One number that is wildly off beats ten small tweaks.
5. **Read the history.** Keep a queue file of ideas with their status (tried, adopted, rejected, and why). Don't retry a rejected idea unchanged.

## The loop

1. Change one thing.
2. **Prove it's exact first, then time it.** `speedloop golden check OUTPUTS goldens/<name>` must say IDENTICAL. When the change is rounding-level by nature (reordered sums, float32 for float64), report the changed-value count, max difference and 99.9th percentile, and get the owner's OK before keeping it. Anything visible or clearly different is rejected, whatever the speed.
3. **Test by risk.**
   - **Pure refactor or exact cache:** exactness check plus one quick paired timing.
   - **Threading, scheduling or memory changes:** also check concurrency and peak memory.
   - **The full measurement** (A/A plus at least two ABBA pairs, on every target, at every CPU count you ship on) runs once, on the final combined batch, not per idea.
4. **Judge with the noise rule.** A delta counts only when it is bigger than the noise AND every candidate run beats every base run. Otherwise it is "noise", even if the median moved.
5. **Caches must pay for themselves.** Every cache is listed in the registry doc with what it holds, its key and invalidation, and its size (`speedloop registry check` enforces this). Keep a cache at ≥ 1 s saved per 100 MiB held, drop it below 0.5 s, and ask the owner in between (`speedloop keep SECONDS MIB`).
6. Commit with the numbers, update the queue file, repeat.

## Batch the expensive part

The full measurement costs the same for one change or ten. Give each round 6-10 targets across the pipeline, prove each change exact as you go, and pay for the full A/A + ABBA measurement once at the end.

## End of round

- Final measurement on the combined batch: `speedloop bench --pairs 2 --aa-pairs 1 --out results-<round>/receipt.json`, plus `speedloop ledger` for a per-step breakdown against the base.
- All goldens IDENTICAL (or the owner-approved rounding changes, counted).
- Report: a table of target | base | candidate | delta | noise | verdict, then peak memory, then every idea not finished and every new idea (into the queue file).
- Never report "faster" for a row whose verdict is "noise".

## Measurement hygiene

- Keep the machine quiet while timing: no test suites, builds or other benchmarks running beside it.
- Interleave base and candidate (ABBA), never "all of A, then all of B".
- Measure real end-to-end latency where users feel it, not the sum of stage timings.
- Record the environment (CPU count, versions, commit) with every receipt; `speedloop bench` does this.

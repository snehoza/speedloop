---
name: speed-loop
description: Run a speed-optimisation round on any codebase the measured way - profile, change one thing, prove the output did not change, and keep only gains bigger than run-to-run noise, with memory as a target too. Use when asked to make code faster, run a speed round, or check whether a change really is faster.
---

# Speed loop

A round makes the code faster without changing what it produces. Every claimed gain survives a noise check, and memory is a target too, not an afterthought. The tool is the `speedloop` package (`python -m speedloop --help`).

## Before the first change

1. **Set up the files once.** `speedloop init docs/speed` writes three files:
   - `SPEED_QUEUE.md`: the one list of ideas, with adopted and rejected ones and why;
   - `ROUND_REPORT.md`;
   - `CACHES.md`.

   Read the queue before proposing anything, and don't retry a rejected idea unchanged.
2. **Name the targets.** Pick the user-facing actions that matter (a request, a page load, a batch job) and their metrics: wall time and peak memory, plus anything the program reports through `SPEEDLOOP_METRIC name=value`.
3. **Freeze the output.** Run the current code on a fixed set of inputs, then `speedloop golden build OUTPUTS goldens/<name>`. Goldens are never overwritten; a new golden gets a new folder.
4. **Measure the noise.** `speedloop bench` always includes A/A pairs (`--aa-pairs`). A change smaller than that spread is not a result.
5. **Profile, don't guess.**
   - `speedloop profile script.py` (cProfile) for a first look.
   - The `Tracer` (`speedloop.trace`) and `speedloop ledger` for a per-step breakdown of each user action.
   - Start with the one number that is wildly off. It beats ten small tweaks.

## The loop

1. Change one thing.
2. **Prove it's exact first, then time it.**
   - Set the output tolerance once per project, in the output's units (`--adopt-max`, `--ask-max`), or relative (`--relative`) for floating-point results.
   - `speedloop golden check OUTPUTS goldens/<name>` works on arrays, images, JSON, CSV and text:
     - `identical`: fine.
     - `adopt` (within the adopt tolerance, rounding level): count it and report it.
     - `ask`: run `speedloop golden review ... --out review/`, look at the diffs or full-size image sheets, and get the owner's OK before keeping it.
     - `reject`: drop it, whatever the speed.
   - Changes to decisions (labels, categories, which branch was taken, the shape of the output) count as real changes, however small the numbers.
3. **Test by risk.**
   - **Kernel or pure-function change:** `speedloop.micro.compare(old, new, make_input=...)` is the proof and the estimate. No end-to-end run needed.
   - **Exact refactor or exact cache:** golden check plus one quick `bench --pairs 1 --aa-pairs 0` screen, or `speedloop compare` against the last round's receipt.
   - **Threading, scheduling or memory changes:** also `--parallel 2` (load) and peak memory.
   - **The full measurement:** A/A plus at least two ABBA pairs, on every target, at every CPU count you ship on (`--cpus 4,16`). It runs once, on the final combined batch, not per idea.
4. **Judge with the noise rule.** A delta counts only when it is bigger than the noise AND every candidate run beats every base run. Otherwise it is `noise`, even if the median moved. Memory uses the same rule.
5. **Caches must pay for themselves.**
   - Every cache gets a row in `CACHES.md`: what it holds, its key and invalidation, its size, and what it saves. `speedloop registry check` enforces this in CI.
   - `speedloop keep SECONDS MIB` applies the rule: keep at ≥ 1 s saved per 100 MiB held, drop below 0.5 s, ask the owner in between.
   - Limit each change's memory growth. +100 MiB and +5% peak is a sensible default.
6. Commit with the numbers, update the queue, repeat.

## Batch the expensive part

The full measurement costs the same for one change or ten. Give each round 6-10 targets across the pipeline, prove each change exact as you go, and pay for the full A/A + ABBA measurement once, at the end.

## End of round

- Final `speedloop bench --pairs 2 --aa-pairs 1 --cpus 4,16 --out results-<round>/receipt.json`, plus `speedloop ledger` against the base for the per-step breakdown.
- Every golden `identical`, or `adopt` / owner-approved `ask` changes, counted.
- Fill in `ROUND_REPORT.md`:
  - the verdict first;
  - a table of target | CPUs | A/A spread | base | candidate | delta | verdict, then memory;
  - every idea not finished and every new idea, moved into `SPEED_QUEUE.md`.
- Never report "faster" for a row whose verdict is `noise`.

## Measurement hygiene

- **One timing job at a time.** `bench` holds the heavy-job lock and waits for an idle machine. Run anything else heavy (test suites, builds, other benchmarks) through `speedloop locked -- CMD`, never beside a measurement.
- **Interleave base and candidate (ABBA),** never "all of A, then all of B".
- **Measure real end-to-end latency** where users feel it, not the sum of stage timings, and with background work switched on if the product runs it.
- **Keep the receipts.** Each one records the environment (CPU count, versions, commit, how busy the machine was).

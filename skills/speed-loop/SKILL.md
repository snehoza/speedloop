---
name: speed-loop
description: Make code faster the measured way - profile, change one thing, prove the output did not change, and keep only gains bigger than run-to-run noise, with peak memory as a target too. Use when asked to speed up code, run an optimisation round, benchmark a change, or check whether a change really is faster or uses less memory.
license: MIT
compatibility: Any agent that can run shell commands. Needs Python 3.11+ and the speedloop package (pip install speedloop, or PYTHONPATH pointing at the speed-loop repo); numpy and Pillow are optional, for comparing array and image outputs.
metadata:
  version: "1.0.0"
  repository: speed-loop
---

# Speed loop

A round makes the code faster without changing what it produces. Every claimed gain survives a noise check, and memory is a target too. The tool is the `speedloop` command (`python -m speedloop --help`). Every command and option is in [the command reference](references/COMMANDS.md).

Decisions marked **ask the user** belong to whoever owns the code. Stop and ask; don't decide them yourself.

## Before the first change

1. **Set up the files once.** `speedloop init docs/speed` writes three files:
   - `SPEED_QUEUE.md`: the one list of ideas, with adopted and rejected ones and why;
   - `ROUND_REPORT.md`;
   - `CACHES.md`.

   Read the queue before proposing anything. Don't retry a rejected idea unchanged.
2. **Name the targets.** Pick the user-facing actions that matter (a request, a page load, a batch job) and their metrics: wall time and peak memory, plus anything the program prints as `SPEEDLOOP_METRIC name=value`.
3. **Freeze the output.** Run the current code on a fixed set of inputs, then `speedloop golden build OUTPUTS goldens/<name>`. Goldens are never overwritten; a new golden gets a new folder.
4. **Set the output tolerance (ask the user once).** Two limits, in the output's own units or relative (`--relative`):
   - **adopt:** a difference nobody needs to look at;
   - **ask:** the most a person may still approve.

   The defaults, 1 and 3, suit integer outputs such as images.
5. **Measure the noise.** `speedloop bench` always includes A/A pairs. A change smaller than that spread is not a result.
6. **Profile, don't guess.**
   - `speedloop profile script.py` (cProfile) for a first look.
   - For a per-step breakdown of each user action, instrument with `speedloop.trace.Tracer` and read it with `speedloop ledger`.
   - Start with the one number that is wildly off. It beats ten small tweaks.

## The loop

1. **Change one thing.**
2. **Prove it's exact first, then time it.** `speedloop golden check OUTPUTS goldens/<name>` works on arrays, images, JSON, CSV and text:
   - `identical`: fine.
   - `adopt`: within the adopt tolerance. Count it and report it.
   - `ask`: run `speedloop golden review OUTPUTS goldens/<name> --out review/`, show the user the diffs or full-size image sheets, and **ask the user** before keeping it.
   - `reject`: drop the change, whatever the speed.
   - Changed labels, categories, branches taken or output shapes are real changes, however small the numbers.
3. **Test by risk.**
   - **Kernel or pure-function change:** `speedloop.micro.compare(old, new, make_input=...)` is the proof and the estimate.
   - **Exact refactor or exact cache:** golden check plus one quick `speedloop bench --pairs 1 --aa-pairs 0`, or `speedloop compare` against the last round's receipt.
   - **Threading, scheduling or memory change:** also `--parallel 2` (load) and the peak-memory row.
   - **The full measurement:** A/A plus at least two ABBA pairs, every target, every CPU count the code ships on (`--cpus 4,16`). It runs once, on the final combined batch, not per idea.
4. **Judge with the noise rule.** A delta counts only when it is bigger than the noise AND every candidate run beats every base run. Otherwise the verdict is `noise`, even if the median moved. Memory uses the same rule.
5. **Caches must pay for themselves.**
   - Every cache gets a row in `CACHES.md`: what it holds, its key and invalidation, its size and what it saves. `speedloop registry check` enforces this.
   - `speedloop keep SECONDS MIB`: keep at ≥ 1 s saved per 100 MiB held, drop below 0.5 s; in between, **ask the user**.
   - Keep each change's memory growth small. Unless the user says otherwise, more than +100 MiB or +5% peak needs their OK.
6. **Commit with the numbers,** update the queue, repeat.

## Batch the expensive part

The full measurement costs the same for one change or ten. Give a round several targets across the code, prove each change exact as you go, and pay for the full A/A + ABBA measurement once, at the end.

## End of round

- Final `speedloop bench --pairs 2 --aa-pairs 1 --out results-<round>/receipt.json` (add `--cpus` for each CPU count that matters), plus `speedloop ledger` against the base for the per-step breakdown.
- Every golden `identical`, `adopt` (counted), or `ask` that the user approved.
- Fill in `ROUND_REPORT.md`:
  - the verdict first;
  - target | CPUs | A/A spread | base | candidate | delta | verdict, then memory;
  - every unfinished or new idea, moved into `SPEED_QUEUE.md`.
- Never report "faster" for a row whose verdict is `noise`.

## Measurement hygiene

- **One timing job at a time.** `bench` holds a heavy-job lock and waits for an idle machine. Run anything else heavy (test suites, builds, other benchmarks) through `speedloop locked -- CMD`, never beside a measurement.
- **Interleave base and candidate (ABBA),** never "all of A, then all of B".
- **Measure end-to-end latency** where users feel it, not the sum of stage timings.
- **GPU code must synchronise** before reading a clock.
- **Keep the receipts:** each records the environment, the commit and how busy the machine was.

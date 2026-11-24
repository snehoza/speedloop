# Speed Loop

Make code faster and be able to prove it: every claimed gain is bigger than run-to-run noise, the output didn't change, and memory didn't quietly grow.

Most "it's 12% faster now" claims come from timing the old version a few times, then the new one a few times, and comparing averages. On a real machine, the same code easily varies by more than that between runs, and a background build or a test suite running beside the benchmark can move the numbers further. Speed Loop turns the careful way into a handful of commands, plus a [Claude Code skill](skills/speed-loop/SKILL.md) that runs whole optimisation rounds with them.

| Command | What it does |
| --- | --- |
| `bench` | Runs old and new interleaved (A-B-B-A) plus old against itself (A/A) for the noise floor. Records time, **peak memory** and your own metrics, and gives each a verdict: `faster`, `SLOWER` or `noise`. Can pin to N cores (`--cpus 4,16`) and run copies side by side (`--parallel`). |
| `golden` | Snapshots outputs once and checks every change against them: byte-identical, or how many values moved and by how much, with a budget verdict (`adopt` / `ask` / `reject`) and full-size before/after/diff sheets for images. |
| `ledger` | Turns span traces into a time tree per user action, with a noise-aware delta for every step. `speedloop.trace.Tracer` records the traces. |
| `micro` (Python API) | Microbenchmark of two functions on the same input: exactness first, then A/A + ABBA timing. |
| `registry`, `keep` | Every cache in the code must be documented, and must save at least 1 s per 100 MiB it holds. |
| `locked`, quiet check | One timing job at a time. `bench` waits for an idle machine, and anything else heavy can run under the same lock. |
| `compare`, `profile`, `init` | Screen against an earlier receipt, cProfile a program, and start a project's queue / round-report / cache-registry files. |

Standard library only, Python 3.11+, Linux / Windows / macOS. numpy (and Pillow, for images) are optional and turn on numeric output comparison.

## Quick start

```bash
pip install -e ".[numeric]"     # or run in place: PYTHONPATH=. python -m speedloop ...

# 1. freeze the current output
python examples/demo/work.py --impl slow --out results-demo/slow
speedloop golden build results-demo/slow results-demo/golden

# 2. make the change, prove the output is the same
python examples/demo/work.py --impl fast --out results-demo/fast
speedloop golden check results-demo/fast results-demo/golden      # VERDICT: IDENTICAL

# 3. time it against the noise
speedloop bench --base "python examples/demo/work.py --impl slow" \
                --cand "python examples/demo/work.py --impl fast" --pairs 2 --aa-pairs 1
```

```text
| metric       | A/A spread | base median | candidate median | delta   | noise   | verdict |
| histogram_s  | 0.03025    | 0.5117      | 0.02237          | -0.4894 | 0.03025 | faster  |
| peak_rss_mib | 0.01172    | 9.391       | 9.391            | +0      | 0.01172 | noise   |
| wall_s       | 0.03921    | 0.6807      | 0.1847           | -0.496  | 0.03921 | faster  |
```

## The noise rule

A change counts only when both hold:

1. **It's bigger than the noise.** The median moved by more than the largest of: the base runs' spread, the candidate runs' spread, and the A/A spread.
2. **The two sides separate.** Every candidate run beats every base run (or every one is worse).

Otherwise the verdict is `noise`, however good the median looks. With fewer than two runs on a side there is no noise estimate, so nothing counts.

## bench

```bash
speedloop bench --base CMD --cand CMD [--pairs 2] [--aa-pairs 1] [--warmup 1]
                [--cpus 4,16] [--parallel 2] [--timeout S] [--out receipt.json]
```

- **Order.** A/A pairs first (`A A2 A2 A`), then ABBA pairs. Warm-up runs of both sides are not timed.
- **Metrics.** Every run records `wall_s` and `peak_rss_mib`. A command adds its own by printing `SPEEDLOOP_METRIC name=value` lines; each gets its own verdict. Lower is better unless you list the metric in `--higher-is-better`.
- **Peak memory.**
  - **Linux and macOS:** covers the child and every descendant it waited for.
  - **Windows:** covers the direct child only, so call the real program (`python script.py`), not a launcher (`py script.py`).
- **`--cpus 4,16`.** Runs the whole schedule once per count, with a table and receipt each. Each run is pinned to that many cores, and `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `NUMBA_NUM_THREADS` and friends are set to match.
  - **Linux:** `sched_setaffinity`.
  - **Windows:** the child inherits the mask, so it never runs unpinned. Up to 64 logical CPUs.
  - **macOS:** has no affinity API, so only the thread variables apply, with a warning.
- **`--parallel N`.** Starts N copies of each run at once, to catch changes that win alone but lose under load. Each metric is the worst copy, and `peak_rss_mib_total` is the sum.
- **Quiet machine.** The whole schedule holds the heavy-job lock (`~/.speedloop/heavy.lock`, or `$SPEEDLOOP_LOCK`).
  - Before each run, `bench` waits until system CPU use is below `--quiet-threshold` (default 25%), for up to `--quiet-wait` seconds.
  - The value is recorded in the receipt, and the report warns if a run started on a busy machine.
  - Run test suites and builds beside a measurement with `speedloop locked -- CMD`, so they wait their turn.
  - `--no-lock` and `--quiet-threshold -1` switch these off.
- **Screening against an earlier receipt.** `speedloop compare old.json new.json` compares a new candidate with a base measured earlier, for example last round's. The two sides weren't interleaved, so treat it as a screen and confirm with a `bench` before claiming a gain.

## golden

```bash
speedloop golden build OUTPUTS GOLDEN                # never overwrites; a new golden gets a new folder
speedloop golden check OUTPUTS GOLDEN [--strict]     # exit 0 for identical / adopt
speedloop golden sheet OUTPUTS GOLDEN --out SHEETS   # before | after | diff, full resolution
```

`.npy`, `.npz` and images are compared numerically (with numpy, plus Pillow for images). For each changed file you get the count of changed values, the largest difference and the 99.9th percentile, and a verdict:

| verdict | largest difference | meaning |
| --- | --- | --- |
| `identical` | 0 | same values |
| `adopt` | ≤ 1 (`--adopt-max`) | rounding level: count it and report it |
| `ask` | ≤ 3 (`--ask-max`) | a person looks at the sheets and decides |
| `reject` | more, or a shape change | not a speed change any more |

Look at sheets at full size; small changes are invisible in thumbnails, in both directions.

## Span ledger and tracer

```python
from speedloop.trace import Tracer

tracer = Tracer()
with tracer.action("load_page"):          # one user-facing action = one tree
    with tracer.span("fetch"):
        ...
    with tracer.span("parse"):
        ...
tracer.save("results-round1/trace.json")
```

`@tracer.timed()` wraps a function in a span. Spans record their thread, so work on a worker thread shows up as "beside" work: it overlaps the action rather than adding to it.

```bash
speedloop ledger --cand new1.json new2.json --base old1.json old2.json --aa old3.json old4.json --out ledger.md
```

The ledger prints one tree per action:
- each span's inclusive time, own time, call count and run-to-run range;
- work on other threads, listed separately;
- time no span covers;
- a verdict per row against the base.

You can also write the trace JSON yourself: `{"runs": [{"action", "start", "end", "spans": [{"name", "start", "end", "thread"}]}]}`.

## Microbenchmarks

For kernel-level changes, a byte-compare plus a microbenchmark is the proof and the estimate:

```python
from speedloop import micro

r = micro.compare(old_kernel, new_kernel, make_input=lambda: load_test_array(), pairs=5)
print(micro.report(r))      # "Exact: identical results." + the verdict table
```

Results are compared first. The comparison handles numpy arrays (shape, dtype and values), nested lists, tuples and dicts, and plain values. The two functions are then timed in A/A + ABBA order with the garbage collector paused.

## Caches must pay for themselves

```toml
# pyproject.toml
[tool.speedloop.registry]
doc = "docs/speed/CACHES.md"
code = ["src/**/*.py"]
pattern = "['\"](cache_[a-z0-9_]+)['\"]"
row = "^\\| `([A-Za-z0-9_.]+)` \\|"
```

`speedloop registry check` fails on any cache name found in code but missing from the table, or listed in the table but gone from the code. Run it in CI.

`speedloop keep 2.4 180` → `keep: 1.33 s saved per 100 MiB`. The rule:
- **keep** a cache that saves at least 1 s for every 100 MiB it holds;
- **drop** one that saves under 0.5 s;
- in between, a person decides.

## Running rounds

`speedloop init docs/speed` writes three files:
- `SPEED_QUEUE.md`: the one list of ideas, in progress, adopted, and rejected with the reason;
- `ROUND_REPORT.md`;
- `CACHES.md`.

The [speed-loop skill](skills/speed-loop/SKILL.md) describes the whole round:
1. freeze the output and measure the noise;
2. profile and fix the biggest hot spot first;
3. test by risk;
4. batch the expensive measurement;
5. report only what clears the noise.

Copy `skills/speed-loop` into `.claude/skills/` (project) or `~/.claude/skills/` (personal) to use it with Claude Code.

## Where this comes from

These tools and rules come out of a long optimisation effort on an image-processing pipeline. Many rounds made the pipeline several times faster with byte-identical output, and a few changes that looked like wins turned out to be noise.

## License

MIT

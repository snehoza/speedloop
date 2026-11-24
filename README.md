# Speed Loop

Make code faster and be able to prove it: every claimed gain is bigger than run-to-run noise, and the output didn't change.

Most "it's 12% faster now" claims come from timing the old version a few times, then the new one a few times, and comparing averages. On a real machine, the same code easily varies by more than that between runs. Speed Loop turns the careful way into a few commands:

- **`bench`** runs the old and new version interleaved (A-B-B-A), plus the old version against itself (A/A) to measure the noise. Then it gives each metric a verdict: `faster`, `SLOWER` or `noise`.
- **`ledger`** turns span traces into a time tree per user action, with a noise-aware delta for every step against a base.
- **`golden`** snapshots outputs once, then checks every change against them: byte-identical, or exactly how many values moved and by how much.
- **`registry`** keeps a documented list of every cache in the code and fails when one is missing or stale. **`keep`** decides whether a cache is worth its memory.
- **`skills/speed-loop`** is the whole round process, written as a Claude Code skill.

Standard library only, Python 3.11+. numpy (and Pillow, for images) are optional; with them, `golden check` reports numeric differences.

## Quick start

```bash
pip install -e .            # or run in place: PYTHONPATH=. python -m speedloop ...

# 1. freeze the current output
python examples/demo/work.py --impl slow --out results-demo/slow
speedloop golden build results-demo/slow results-demo/golden

# 2. make the change, prove the output is the same
python examples/demo/work.py --impl fast --out results-demo/fast
speedloop golden check results-demo/fast results-demo/golden      # IDENTICAL

# 3. time it against the noise
speedloop bench --base "python examples/demo/work.py --impl slow" \
                --cand "python examples/demo/work.py --impl fast" --pairs 2 --aa-pairs 1
```

```text
| metric      | A/A spread | base median | candidate median | delta   | noise   | verdict |
| histogram_s | 0.04016    | 0.4893      | 0.0231           | -0.4662 | 0.04016 | faster  |
| wall_s      | 0.04117    | 0.6589      | 0.1852           | -0.4737 | 0.04117 | faster  |
```

## The noise rule

A change counts only when both hold:

1. **It's bigger than the noise.** The median moved by more than the largest of: the base runs' spread, the candidate runs' spread, and the A/A spread.
2. **The two sides separate.** Every candidate run is faster than every base run (or every one is slower).

Otherwise the verdict is `noise`, however good the median looks. With fewer than two runs on a side there is no noise estimate, so nothing counts.

## Reporting your own metrics

`bench` always records each run's wall time. A command can also print any metric it measures itself, and each one gets its own verdict:

```text
SPEEDLOOP_METRIC parse_s=0.412
SPEEDLOOP_METRIC peak_mib=812
```

Use `--higher-is-better` for metrics such as throughput.

## Span ledger

Write traces as JSON (one or more runs per file):

```json
{"runs": [{"action": "load_page", "start": 0.0, "end": 1.84,
           "spans": [{"name": "fetch", "start": 0.01, "end": 0.9, "thread": "main"}]}]}
```

`speedloop ledger --cand new1.json new2.json --base old1.json old2.json --aa old3.json old4.json --out ledger.md`
prints one tree per action:
- inclusive and self time, call count and run-to-run range for each span;
- work on other threads, listed separately because it overlaps the action rather than adding to it;
- time no span covers;
- a verdict per row against the base.

## Cache registry

```toml
[tool.speedloop.registry]
doc = "docs/CACHES.md"
code = ["src/**/*.py"]
pattern = "['\"](cache_[a-z0-9_]+)['\"]"
row = "^\\| `([A-Za-z0-9_.]+)` \\|"
```

`speedloop registry check` fails on any cache name found in code but not documented, or documented but gone from the code. Run it in CI.

`speedloop keep 2.4 180` → `keep: 1.33 s saved per 100 MiB`. The rule:
- **keep** a cache that saves at least 1 s for every 100 MiB it holds;
- **drop** one that saves under 0.5 s;
- in between, **ask** someone.

## Where this comes from

These tools and rules come out of a long optimisation effort on an image-processing pipeline. Many rounds made the pipeline several times faster with byte-identical output, and a few changes that looked like wins turned out to be noise.

## License

MIT

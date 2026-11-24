# speedloop command reference

Run `speedloop COMMAND --help` for every option. Exit code 0 means success; `golden check` and `registry check` return 1 on failure, so they work as CI gates.

## Setup

| Command | Purpose |
| --- | --- |
| `speedloop init [DIR]` | Write `SPEED_QUEUE.md`, `ROUND_REPORT.md` and `CACHES.md` templates (default `docs/speed`). Existing files are kept. |

## Timing

`speedloop bench --base CMD --cand CMD [options]`

| Option | Default | Meaning |
| --- | --- | --- |
| `--pairs N` | 2 | ABBA pairs (base, candidate, candidate, base) |
| `--aa-pairs N` | 1 | A/A pairs (base against itself) for the noise floor |
| `--warmup N` | 1 | untimed runs of each side first |
| `--cpus 4,16` | none | run the whole schedule once per count, pinned to that many cores, thread-pool variables set to match (Linux, Windows; macOS: thread caps only) |
| `--parallel N` | 1 | N copies at once (load check); each metric is the worst copy, `peak_rss_mib_total` the sum |
| `--timeout S` | none | seconds per run |
| `--higher-is-better M ...` | none | metrics where bigger is better (throughput) |
| `--no-lock` | off | skip the heavy-job lock (`~/.speedloop/heavy.lock`, or `$SPEEDLOOP_LOCK`) |
| `--quiet-threshold P` | 25 | before each run, wait until system CPU use is below P % (-1 disables) |
| `--quiet-wait S` | 60 | total waiting budget per schedule |
| `--out FILE` | none | save the receipt (JSON); with several `--cpus`, one file per count |

**Metrics.**
- Every run records `wall_s` and `peak_rss_mib`.
  - Linux and macOS: the child plus the descendants it waited for.
  - Windows: the direct child only, so call `python script.py`, not a launcher.
- The command adds its own metrics by printing `SPEEDLOOP_METRIC name=value` lines.

**Report columns:** metric | A/A spread | base median | candidate median | delta | delta % | noise | verdict (`faster`, `SLOWER`, `noise`, `too few runs`).

| Command | Purpose |
| --- | --- |
| `speedloop report RECEIPT` | Print a saved receipt's table again. |
| `speedloop compare BASE.json CAND.json [--base-arm A --cand-arm B]` | Screen a candidate against an earlier receipt. Not interleaved, so it's weaker evidence; confirm with `bench`. |

## Exactness

| Command | Purpose |
| --- | --- |
| `speedloop golden build OUTPUTS GOLDEN` | Snapshot outputs with a SHA-256 manifest. Refuses to overwrite. |
| `speedloop golden check OUTPUTS GOLDEN [--adopt-max X --ask-max Y --relative --strict]` | Verdict per file and overall: `identical` / `adopt` / `ask` / `reject`. Exit 0 for identical or adopt (`--strict`: identical only). |
| `speedloop golden review OUTPUTS GOLDEN --out DIR [--amplify N]` | For each changed file, a unified diff (text, JSON, CSV) or a full-resolution golden / new / difference image sheet. |

| Output kind | Files | Compared |
| --- | --- | --- |
| array | `.npy` `.npz` images | every value (numpy; images also Pillow) |
| json | `.json` | structure and keys must match; numbers as values |
| table | `.csv` `.tsv` | shape must match; numeric cells as values, text cells equal |
| text | other UTF-8 | changed lines counted (always `ask`) |
| binary | anything else | differs (always `ask`) |

Structure changes, changed non-numeric values and missing files are `reject`. New files are `ask`.

## Breakdown and profiling

| Command / API | Purpose |
| --- | --- |
| `speedloop.trace.Tracer` | `with tracer.action("name"):` around one user action, `with tracer.span("step"):` inside it, `@tracer.timed()` on functions, then `tracer.save(path)`. |
| `speedloop ledger --cand T.json ... [--base ...] [--aa ...] [--main-thread NAME] [--min-ms 5] [--out ledger.md]` | Time tree per action: inclusive, own time, calls, run-to-run range, work on other threads ("beside"), unspanned time, verdict per row. |
| `speedloop profile [--top 25] [--out FILE.prof] script.py ARGS` or `-m module ARGS` | cProfile in a child process; prints the top functions by cumulative and own time. |
| `speedloop.micro.compare(old, new, make_input=..., pairs=5)` | In-process: exactness check on fresh inputs, then A/A + ABBA timing, auto-calibrated to at least 50 ms per sample. `micro.report(r)` prints the result. |

## Caches and the machine

| Command | Purpose |
| --- | --- |
| `speedloop registry check [--root .]` | Every cache name in code is documented in the table, and every row still exists in code. Configured in `pyproject.toml` under `[tool.speedloop.registry]` (`doc`, `code`, `pattern`, `row`, optional `exempt`). |
| `speedloop keep SECONDS MIB` | `keep` (≥ 1 s per 100 MiB), `drop` (< 0.5 s) or `ask`. |
| `speedloop locked -- CMD ...` | Run any command under the heavy-job lock, so it never overlaps a measurement. |

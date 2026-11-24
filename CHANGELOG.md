# Changelog

## 1.0.0 (review fixes)

- Windows: `--cpus` with `--parallel` no longer races on the parent's own affinity mask.
- Ledger: the main thread is chosen once per action across all runs, so every run's tree is comparable.
- Quiet-machine wait: one budget per schedule (a busy CI runner no longer waits before every run).
- Reports gain a delta % column. `golden check` prints a readable line per changed file and a summary.
- `micro`:
  - auto-calibrates the loop count (at least 50 ms per sample);
  - the exactness check uses fresh inputs per side.
- Registry `exempt` list.
- Packaging:
  - setuptools >= 77 (SPDX licence string);
  - classifier Beta;
  - macOS added to CI.

## 1.0.0 (features)

- `bench`:
  - peak memory per run (`peak_rss_mib`), judged by the same noise rule;
  - `--parallel N` load check;
  - heavy-job lock held for the whole schedule;
  - waits for a quiet machine before each run, recorded in the receipt;
  - `--cpus` pinning on Windows as well as Linux (macOS: thread caps only).
- `golden`:
  - content comparison for any output (arrays, images, JSON, CSV/TSV, text, binary);
  - a tolerance verdict per file (`identical` / `adopt` / `ask` / `reject`), absolute or `--relative`;
  - `review` writes diffs and full-resolution image sheets;
  - `--strict`.
- New:
  - `micro` (in-process microbenchmark with an exactness check);
  - `compare` (screen against an earlier receipt);
  - `profile` (cProfile summary);
  - `locked` (run anything under the heavy-job lock);
  - `init` (queue, round-report and cache-registry templates).
- Skill rewritten around the full toolset.

## 0.1.0

- `bench` (A/A + ABBA, noise rule), `ledger`, `golden` build/check, `registry`, `keep`, tracer, `--cpus` (Linux), demo, skill.

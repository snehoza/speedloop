"""speedloop command line.

  speedloop init [DIR]                          queue, round-report and cache-registry templates
  speedloop bench --base CMD --cand CMD [--pairs 2 --aa-pairs 1] [--cpus 4,16] [--parallel 2] --out R.json
  speedloop report R.json                       re-print a receipt's verdict table
  speedloop compare BASE.json CAND.json         screen a candidate against an earlier receipt
  speedloop ledger --cand T.json ... [--base ...] [--aa ...]
  speedloop golden build OUTPUTS GOLDEN | check OUTPUTS GOLDEN | review OUTPUTS GOLDEN --out DIR
  speedloop registry check [--root .]
  speedloop keep SECONDS_SAVED MIB_HELD
  speedloop profile [-m module | script.py] [args ...]
  speedloop locked -- CMD ...                   run anything under the heavy-job lock
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from importlib import resources
from pathlib import Path

from . import bench, exact, ledger, machine, profile, registry

TEMPLATES = ("SPEED_QUEUE.md", "ROUND_REPORT.md", "CACHES.md")


def _split(cmd: str) -> list[str]:
    return shlex.split(cmd, posix=(sys.platform != "win32"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rest(items: list[str]) -> list[str]:
    return items[1:] if items[:1] == ["--"] else items


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="speedloop", description="Noise-aware speed measurement with exactness checks.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="copy the queue / round-report / cache-registry templates into a folder")
    i.add_argument("dir", type=Path, nargs="?", default=Path("docs/speed"))

    b = sub.add_parser("bench", help="A/A + ABBA timing of two commands")
    b.add_argument("--base", required=True, help="command for the current code (A)")
    b.add_argument("--cand", required=True, help="command for the changed code (B)")
    b.add_argument("--pairs", type=int, default=2, help="ABBA pairs (default 2)")
    b.add_argument("--aa-pairs", type=int, default=1, help="A/A pairs for the noise floor (default 1)")
    b.add_argument("--warmup", type=int, default=1, help="untimed runs of each side first (default 1)")
    b.add_argument("--timeout", type=float, default=None, help="seconds per run")
    b.add_argument("--cpus", default="", help="comma list of CPU counts, e.g. 4,16: the whole schedule runs once per "
                   "count, pinned to that many cores (Linux, Windows; macOS: thread caps only)")
    b.add_argument("--parallel", type=int, default=1,
                   help="run N copies at once (a load check); each metric is the worst copy")
    b.add_argument("--higher-is-better", nargs="*", default=[], help="metrics where bigger is better")
    b.add_argument("--no-lock", action="store_true", help="do not take the heavy-job lock")
    b.add_argument("--lock-timeout", type=float, default=None, help="give up waiting for the lock after N seconds")
    b.add_argument("--quiet-threshold", type=float, default=25.0,
                   help="before each run, wait until CPU use is below this %% (default 25; -1 disables)")
    b.add_argument("--quiet-wait", type=float, default=60.0, help="longest wait for a quiet machine, seconds")
    b.add_argument("--out", type=Path, help="receipt path; with several --cpus, one file per count (name-<n>cpu.json)")

    r = sub.add_parser("report", help="re-print the verdict table of a bench receipt")
    r.add_argument("receipt", type=Path)
    r.add_argument("--higher-is-better", nargs="*", default=[])

    c = sub.add_parser("compare", help="screen a candidate receipt against an earlier base receipt (not interleaved)")
    c.add_argument("base", type=Path)
    c.add_argument("cand", type=Path)
    c.add_argument("--base-arm", default="A")
    c.add_argument("--cand-arm", default="B")
    c.add_argument("--higher-is-better", nargs="*", default=[])

    lg = sub.add_parser("ledger", help="span time tree per action, with noise-aware deltas")
    lg.add_argument("--cand", nargs="+", required=True)
    lg.add_argument("--base", nargs="*", default=[])
    lg.add_argument("--aa", nargs="*", default=[], help="base-vs-base traces for an A/A spread")
    lg.add_argument("--main-thread")
    lg.add_argument("--min-ms", type=float, default=5.0)
    lg.add_argument("--out", type=Path)

    g = sub.add_parser("golden", help="golden outputs: build once, check after every change, review the differences")
    g.add_argument("action", choices=["build", "check", "review"])
    g.add_argument("outputs", type=Path)
    g.add_argument("golden", type=Path)
    g.add_argument("--adopt-max", type=float, default=exact.ADOPT_MAX,
                   help="largest difference accepted without review (output units, or a fraction with --relative)")
    g.add_argument("--ask-max", type=float, default=exact.ASK_MAX,
                   help="largest difference a person may still approve; above it the change is rejected")
    g.add_argument("--relative", action="store_true", help="tolerances are relative to the golden value")
    g.add_argument("--strict", action="store_true", help="check: only byte-identical passes")
    g.add_argument("--out", type=Path, help="review: folder for the files to look at (image sheets, text diffs)")
    g.add_argument("--amplify", type=float, default=None, help="review: image differences x N instead of auto-scaled")

    rg = sub.add_parser("registry", help="every cache in code is documented, and vice versa")
    rg.add_argument("action", choices=["check"])
    rg.add_argument("--root", type=Path, default=Path("."))

    k = sub.add_parser("keep", help="is a cache worth its memory? (s saved per 100 MiB)")
    k.add_argument("seconds_saved", type=float)
    k.add_argument("mib_held", type=float)

    p = sub.add_parser("profile", help="cProfile a Python program and print the top functions")
    p.add_argument("--out", type=Path, default=Path("results-profile/profile.prof"))
    p.add_argument("--top", type=int, default=25)
    p.add_argument("target", nargs=argparse.REMAINDER, help="script.py [args] or -m module [args]")

    lk = sub.add_parser("locked", help="run a command under the heavy-job lock (tests, builds beside timing)")
    lk.add_argument("--lock-timeout", type=float, default=None)
    lk.add_argument("command", nargs=argparse.REMAINDER)
    return ap


def _golden(args) -> int:
    if args.action == "build":
        m = exact.build(args.outputs, args.golden)
        print(f"golden built: {len(m['files'])} files in {args.golden}")
        return 0
    result = exact.check(args.outputs, args.golden, args.adopt_max, args.ask_max, args.relative)
    if args.action == "review":
        if not args.out:
            print("review needs --out DIR")
            return 2
        made = 0
        for rel, res in result["files"].items():
            if res["status"] == "differs":
                sub = args.out / Path(rel).parent
                path = exact.review(args.golden / rel, args.outputs / rel, sub, args.amplify)
                if path:
                    print(f"[{res['verdict']}] {path}")
                    made += 1
        print(f"{made} review file(s). Image sheets: golden | new | difference, at full resolution.")
        return 0
    counts: dict = {}
    for rel, res in result["files"].items():
        counts[res["verdict"]] = counts.get(res["verdict"], 0) + 1
        if res["status"] == "identical":
            continue
        d = res.get("diff", {})
        if res["status"] != "differs":
            detail = res["status"]
        elif d.get("kind") == "text":
            detail = f"text: {d['lines_changed']} of {d['lines']} lines changed"
        elif d.get("kind") == "binary":
            detail = "binary: differs"
        elif d.get("structure_changed") or d.get("non_numeric_changed"):
            detail = f"{d['kind']}: structure or non-numeric values changed ({d['non_numeric_changed']} values)"
        else:
            detail = (f"{d['kind']}: {d['changed']} of {d['values']} values changed, "
                      f"max |diff| {d['max_abs']:.4g} (relative {d['max_rel']:.3g})")
        print(f"[{res['verdict']}] {rel}: {detail}")
    print(", ".join(f"{n} {v}" for v, n in sorted(counts.items(), key=lambda kv: exact.ORDER.index(kv[0]))))
    print(f"VERDICT: {result['verdict'].upper()}")
    ok = result["identical"] if args.strict else result["verdict"] in ("identical", "adopt")
    return 0 if ok else 1


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    hib = frozenset(getattr(args, "higher_is_better", None) or [])

    if args.cmd == "init":
        args.dir.mkdir(parents=True, exist_ok=True)
        for name in TEMPLATES:
            dest = args.dir / name
            if dest.exists():
                print(f"kept existing {dest}")
                continue
            text = resources.files("speedloop").joinpath("templates", name).read_text(encoding="utf-8")
            dest.write_text(text, encoding="utf-8")
            print(f"wrote {dest}")
        return 0

    if args.cmd == "bench":
        counts = [int(x) for x in args.cpus.split(",") if x.strip()] or [None]
        quiet = None if args.quiet_threshold < 0 else args.quiet_threshold
        for cpus in counts:
            receipt = bench.run(_split(args.base), _split(args.cand), args.pairs, args.aa_pairs, args.warmup,
                                timeout=args.timeout, cpus=cpus, parallel=args.parallel, lock=not args.no_lock,
                                lock_timeout=args.lock_timeout, quiet_threshold=quiet, quiet_wait=args.quiet_wait)
            if args.out:
                out = args.out if len(counts) == 1 else args.out.with_name(f"{args.out.stem}-{cpus}cpu{args.out.suffix}")
                bench.save(receipt, out)
            print(f"\n## {cpus} CPUs\n" if cpus else "", bench.report(receipt, hib)[0], sep="")
        return 0

    if args.cmd == "report":
        print(bench.report(_load(args.receipt), hib)[0])
        return 0

    if args.cmd == "compare":
        print(bench.compare_receipts(_load(args.base), _load(args.cand), args.base_arm, args.cand_arm, hib)[0])
        return 0

    if args.cmd == "ledger":
        text = ledger.render(ledger.load(args.cand, args.main_thread), ledger.load(args.base, args.main_thread),
                             ledger.load(args.aa, args.main_thread), args.min_ms)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        print(text)
        return 0

    if args.cmd == "golden":
        return _golden(args)

    if args.cmd == "registry":
        result = registry.check(args.root)
        for name in result["undocumented"]:
            print(f"undocumented cache: {name}")
        for name in result["stale"]:
            print(f"stale row (not in code): {name}")
        ok = not result["undocumented"] and not result["stale"]
        print("OK" if ok else "FAIL")
        return 0 if ok else 1

    if args.cmd == "keep":
        decision, rate = registry.keep_rule(args.seconds_saved, args.mib_held)
        print(f"{decision}: {rate:.2f} s saved per 100 MiB "
              f"(keep >= {registry.KEEP_S_PER_100MIB}, drop < {registry.DROP_S_PER_100MIB})")
        return 0

    if args.cmd == "profile":
        target = _rest(args.target)
        if not target:
            print("profile needs a script or -m module")
            return 2
        print(profile.run(target, args.out, args.top))
        print(f"\nfull profile: {args.out}")
        return 0

    if args.cmd == "locked":
        command = _rest(args.command)
        if not command:
            print("locked needs a command after --")
            return 2
        with machine.HeavyLock(timeout=args.lock_timeout, progress=print):
            return subprocess.run(command).returncode
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

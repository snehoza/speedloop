"""speedloop command line.

  speedloop bench --base "python old.py" --cand "python new.py" [--pairs 2 --aa-pairs 1] --out receipt.json
  speedloop report receipt.json
  speedloop ledger --cand c1.json c2.json [--base b1.json b2.json] [--aa a1.json a2.json] [--out ledger.md]
  speedloop golden build OUTPUTS GOLDEN
  speedloop golden check OUTPUTS GOLDEN
  speedloop registry check [--root .]
  speedloop keep SECONDS_SAVED MIB_HELD
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from . import bench, exact, ledger, registry


def _split(cmd: str) -> list[str]:
    return shlex.split(cmd, posix=(sys.platform != "win32"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="speedloop", description="Noise-aware speed measurement with exactness checks.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bench", help="ABBA timing of two commands with an A/A noise floor")
    b.add_argument("--base", required=True)
    b.add_argument("--cand", required=True)
    b.add_argument("--pairs", type=int, default=2)
    b.add_argument("--aa-pairs", type=int, default=1)
    b.add_argument("--warmup", type=int, default=1)
    b.add_argument("--timeout", type=float, default=None)
    b.add_argument("--higher-is-better", nargs="*", default=[], help="metrics where bigger is better")
    b.add_argument("--out", type=Path)

    r = sub.add_parser("report", help="re-print the verdict table of a bench receipt")
    r.add_argument("receipt", type=Path)
    r.add_argument("--higher-is-better", nargs="*", default=[])

    lg = sub.add_parser("ledger", help="span time tree per action, with noise-aware deltas")
    lg.add_argument("--cand", nargs="+", required=True)
    lg.add_argument("--base", nargs="*", default=[])
    lg.add_argument("--aa", nargs="*", default=[], help="base-vs-base traces for an A/A spread")
    lg.add_argument("--main-thread")
    lg.add_argument("--min-ms", type=float, default=5.0)
    lg.add_argument("--out", type=Path)

    g = sub.add_parser("golden", help="golden outputs: build once, check after every change")
    g.add_argument("action", choices=["build", "check"])
    g.add_argument("outputs", type=Path)
    g.add_argument("golden", type=Path)

    rg = sub.add_parser("registry", help="every cache in code is documented, and vice versa")
    rg.add_argument("action", choices=["check"])
    rg.add_argument("--root", type=Path, default=Path("."))

    k = sub.add_parser("keep", help="is a cache worth its memory? (s saved per 100 MiB)")
    k.add_argument("seconds_saved", type=float)
    k.add_argument("mib_held", type=float)

    args = ap.parse_args(argv)

    if args.cmd == "bench":
        receipt = bench.run(_split(args.base), _split(args.cand), args.pairs, args.aa_pairs, args.warmup,
                            timeout=args.timeout)
        if args.out:
            bench.save(receipt, args.out)
        table, _ = bench.report(receipt, frozenset(args.higher_is_better))
        print(table)
        return 0
    if args.cmd == "report":
        table, _ = bench.report(json.loads(args.receipt.read_text(encoding="utf-8")), frozenset(args.higher_is_better))
        print(table)
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
        if args.action == "build":
            m = exact.build(args.outputs, args.golden)
            print(f"golden built: {len(m['files'])} files in {args.golden}")
            return 0
        result = exact.check(args.outputs, args.golden)
        for rel, res in result["files"].items():
            if res["status"] != "identical":
                print(rel, json.dumps(res))
        print("IDENTICAL" if result["identical"] else "DIFFERS")
        return 0 if result["identical"] else 1
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

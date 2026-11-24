import json
import sys
import tempfile
import unittest
from pathlib import Path

import os
import textwrap
import threading

from speedloop import bench, exact, ledger, noise, registry
from speedloop.trace import Tracer


class NoiseTests(unittest.TestCase):
    def test_clear_win_counts(self):
        v = noise.compare([10.0, 10.2, 10.1], [8.0, 8.1, 7.9])
        self.assertEqual(v.verdict, noise.FASTER)
        self.assertTrue(v.counts)

    def test_overlapping_runs_are_noise(self):
        v = noise.compare([10.0, 12.0], [9.0, 11.5])
        self.assertEqual(v.verdict, noise.NOISE)

    def test_aa_spread_raises_the_bar(self):
        base, cand = [10.0, 10.1], [9.7, 9.8]
        self.assertEqual(noise.compare(base, cand).verdict, noise.FASTER)
        self.assertEqual(noise.compare(base, cand, aa_spread=0.5).verdict, noise.NOISE)

    def test_regression_and_higher_is_better(self):
        self.assertEqual(noise.compare([1.0, 1.01], [1.5, 1.51]).verdict, noise.SLOWER)
        self.assertEqual(noise.compare([100, 101], [150, 151], lower_is_better=False).verdict, noise.FASTER)

    def test_single_run_never_counts(self):
        self.assertEqual(noise.compare([10.0], [1.0]).verdict, noise.TOO_FEW)


class BenchTests(unittest.TestCase):
    def test_schedule_is_abba_after_aa(self):
        self.assertEqual(bench.schedule(1, 1), ["A", "A2", "A2", "A", "A", "B", "B", "A"])

    def test_metric_lines(self):
        self.assertEqual(bench.parse_metrics("x\nSPEEDLOOP_METRIC step=0.5\nSPEEDLOOP_METRIC peak_mib=12\n"),
                         {"step": 0.5, "peak_mib": 12.0})

    def test_run_and_report(self):
        py = sys.executable
        receipt = bench.run([py, "-c", "print('SPEEDLOOP_METRIC work=2.0')"],
                            [py, "-c", "print('SPEEDLOOP_METRIC work=1.0')"], pairs=1, aa_pairs=1, warmup=0,
                            progress=lambda *_: None)
        self.assertEqual([r["arm"] for r in receipt["runs"]], bench.schedule(1, 1))
        table, verdicts = bench.report(receipt)
        self.assertIn("| work |", table)
        work = [v for v in verdicts if v.base_median == 2.0][0]
        self.assertEqual(work.verdict, noise.FASTER)


class LedgerTests(unittest.TestCase):
    def _trace(self, d, name, load_ms):
        runs = [{"action": "load", "start": 0.0, "end": 0.2 + load_ms / 1000,
                 "spans": [{"name": "fetch", "start": 0.0, "end": load_ms / 1000, "thread": "main"},
                           {"name": "parse", "start": 0.01, "end": 0.02, "thread": "main"},
                           {"name": "prefetch", "start": 0.0, "end": 0.15, "thread": "worker"}]}
                for _ in range(2)]
        path = Path(d) / name
        path.write_text(json.dumps({"runs": runs}), encoding="utf-8")
        return path

    def test_tree_beside_and_unspanned(self):
        wall, rows = ledger.breakdown({"start": 0, "end": 1.0, "spans": [
            {"name": "outer", "start": 0.0, "end": 0.6, "thread": "main"},
            {"name": "inner", "start": 0.1, "end": 0.3, "thread": "main"},
            {"name": "bg", "start": 0.0, "end": 0.9, "thread": "w"}]}, main_thread="main")
        self.assertAlmostEqual(wall, 1000.0)
        self.assertAlmostEqual(rows[("outer",)][0], 600.0)
        self.assertAlmostEqual(rows[("outer",)][1], 400.0)
        self.assertAlmostEqual(rows[("outer", "inner")][0], 200.0)
        self.assertIn(("[beside: w]", "bg"), rows)
        self.assertAlmostEqual(rows[("(unspanned)",)][0], 400.0)

    def test_render_with_base(self):
        with tempfile.TemporaryDirectory() as d:
            text = ledger.render(ledger.load([self._trace(d, "c.json", 300)]),
                                 ledger.load([self._trace(d, "b.json", 600)]))
        self.assertIn("## load", text)
        self.assertIn("fetch", text)


class ExactTests(unittest.TestCase):
    def test_build_and_check(self):
        with tempfile.TemporaryDirectory() as d:
            out, gold = Path(d, "out"), Path(d, "gold")
            out.mkdir()
            (out / "a.txt").write_text("same", encoding="utf-8")
            (out / "b.txt").write_text("v1", encoding="utf-8")
            exact.build(out, gold)
            self.assertTrue(exact.check(out, gold)["identical"])
            (out / "b.txt").write_text("v2", encoding="utf-8")
            (out / "c.txt").write_text("new", encoding="utf-8")
            res = exact.check(out, gold)
            self.assertFalse(res["identical"])
            self.assertEqual(res["files"]["a.txt"]["status"], "identical")
            self.assertEqual(res["files"]["b.txt"]["status"], "differs")
            self.assertEqual(res["files"]["c.txt"]["status"], "new")
            with self.assertRaises(FileExistsError):
                exact.build(out, gold)

    def test_numeric_diff(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.npy"), Path(d, "b.npy")
            x = np.zeros((10, 10), np.uint8)
            np.save(a, x)
            x[0, 0] = 1
            np.save(b, x)
            stats = exact.numeric_diff(a, b)["array"]
        self.assertEqual((stats["changed"], stats["max_abs"]), (1, 1.0))


class RegistryTests(unittest.TestCase):
    def test_check_and_keep_rule(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("s['cache_thumbs'] = 1\ns['cache_depth'] = 2\n", encoding="utf-8")
            (root / "CACHES.md").write_text("| name | why |\n| --- | --- |\n| `cache_thumbs` | thumbnails |\n"
                                            "| `cache_old` | gone |\n", encoding="utf-8")
            cfg = {"doc": "CACHES.md", "code": ["src/**/*.py"], "pattern": r"['\"](cache_[a-z_]+)['\"]",
                   "row": r"^\| `([A-Za-z0-9_.]+)` \|"}
            result = registry.check(root, cfg)
        self.assertEqual(result, {"undocumented": ["cache_depth"], "stale": ["cache_old"]})
        self.assertEqual(registry.keep_rule(2.0, 100)[0], "keep")
        self.assertEqual(registry.keep_rule(0.7, 100)[0], "ask")
        self.assertEqual(registry.keep_rule(0.1, 100)[0], "drop")


class TraceTests(unittest.TestCase):
    def test_tracer_feeds_the_ledger(self):
        clock = iter([0.0, 0.1, 0.2, 0.3, 0.5, 0.6, 0.9, 1.2]).__next__
        tracer = Tracer(clock=clock)
        with tracer.action("load"):              # 0.0 .. 1.2
            with tracer.span("fetch"):           # 0.1 .. 0.5
                with tracer.span("parse"):       # 0.2 .. 0.3
                    pass
            with tracer.span("render"):          # 0.6 .. 0.9
                pass
        with tempfile.TemporaryDirectory() as d:
            loaded = ledger.load([tracer.save(Path(d, "t.json"))])
        wall, rows = loaded["load"][0]
        self.assertAlmostEqual(wall, 1200.0)
        self.assertAlmostEqual(rows[("fetch",)][0], 400.0)
        self.assertAlmostEqual(rows[("fetch", "parse")][0], 100.0)
        self.assertAlmostEqual(rows[("render",)][0], 300.0)
        self.assertAlmostEqual(rows[("(unspanned)",)][0], 500.0)

    def test_threads_and_decorator(self):
        tracer = Tracer()

        def background():
            with tracer.span("bg"):
                pass

        @tracer.timed()
        def work():
            return 3

        with tracer.action("a"):
            th = threading.Thread(target=background, name="worker")
            th.start()
            th.join()
            self.assertEqual(work(), 3)
        threads = {s["name"]: s["thread"] for s in tracer.runs()[0]["spans"]}
        self.assertEqual(threads["bg"], "worker")
        self.assertIn("TraceTests.test_threads_and_decorator.<locals>.work", threads)


class CpuLimitTests(unittest.TestCase):
    def test_no_limit(self):
        limit = bench.CpuLimit(None)
        self.assertIsNone(limit.env)

    @unittest.skipUnless(hasattr(os, "sched_setaffinity") or sys.platform == "win32", "needs Linux or Windows")
    def test_pinned_child_sees_n_cores(self):
        probe = textwrap.dedent("""
            import os
            if hasattr(os, 'sched_getaffinity'):
                n = len(os.sched_getaffinity(0))
            else:
                import ctypes
                from ctypes import wintypes
                k = ctypes.WinDLL('kernel32')
                k.GetCurrentProcess.restype = wintypes.HANDLE
                a, b = ctypes.c_size_t(), ctypes.c_size_t()
                k.GetProcessAffinityMask(wintypes.HANDLE(k.GetCurrentProcess()), ctypes.byref(a), ctypes.byref(b))
                n = bin(a.value).count('1')
            print(f'SPEEDLOOP_METRIC cores={n}')
            print('SPEEDLOOP_METRIC omp=' + os.environ['OMP_NUM_THREADS'])
        """)
        receipt = bench.run([sys.executable, "-c", probe], [sys.executable, "-c", probe], pairs=1, aa_pairs=0,
                            warmup=0, progress=lambda *_: None, cpus=1)
        a = bench.arms(receipt)["A"]
        self.assertEqual(a["cores"], [1.0, 1.0])
        self.assertEqual(a["omp"], [1.0, 1.0])
        if sys.platform == "win32":   # our own mask is restored after each spawn
            self.assertGreater(bin(bench._win_affinity()).count("1"), 1)


if __name__ == "__main__":
    unittest.main()

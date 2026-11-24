"""Regression tests for the issues found in the 1.0 review."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from speedloop import bench, ledger, machine, micro, proc, registry

PY = sys.executable


class WindowsParallelPinningTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows affinity")
    def test_parallel_pinned_spawns_restore_our_mask(self):
        before = proc.win_affinity()
        if bin(before).count("1") < 2:
            self.skipTest("needs at least 2 CPUs")
        for _ in range(3):
            proc.measure([PY, "-c", "pass"], limit=proc.CpuLimit(1), parallel=4)
        self.assertEqual(proc.win_affinity(), before)


class LedgerThreadTests(unittest.TestCase):
    def test_same_main_thread_for_every_run(self):
        # run 1: the main thread holds the long span; run 2: a worker happens to hold the longest single span,
        # but over both runs "main" holds more time, so both runs must use "main".
        runs = [
            {"action": "x", "start": 0, "end": 1.0, "spans": [
                {"name": "work", "start": 0.0, "end": 0.9, "thread": "main"},
                {"name": "bg", "start": 0.0, "end": 0.2, "thread": "w"}]},
            {"action": "x", "start": 0, "end": 1.0, "spans": [
                {"name": "work", "start": 0.0, "end": 0.5, "thread": "main"},
                {"name": "work2", "start": 0.5, "end": 0.9, "thread": "main"},
                {"name": "bg", "start": 0.0, "end": 0.6, "thread": "w"}]},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "t.json")
            path.write_text(json.dumps({"runs": runs}), encoding="utf-8")
            loaded = ledger.load([path])["x"]
        for _, rows in loaded:
            self.assertIn(("work",), rows)
            self.assertIn(("[beside: w]", "bg"), rows)


class QuietBudgetTests(unittest.TestCase):
    def test_busy_machine_does_not_stall_every_run(self):
        original = machine.cpu_busy
        machine.cpu_busy = lambda interval=0.5: (time.sleep(0.01), 99.0)[1]   # always busy
        try:
            started = time.monotonic()
            r = bench.run([PY, "-c", "pass"], [PY, "-c", "pass"], pairs=1, aa_pairs=1, warmup=0,
                          progress=lambda *_: None, lock=False, quiet_threshold=25, quiet_wait=0.3)
            elapsed = time.monotonic() - started
        finally:
            machine.cpu_busy = original
        self.assertLess(elapsed, 8)                                   # 8 runs, but only 0.3 s of waiting in total
        self.assertTrue(all(run["busy_pct_before"] == 99.0 for run in r["runs"]))
        self.assertIn("busy", bench.report(r)[0])

    def test_delta_percent_column(self):
        receipt = {"runs": [{"arm": "A", "metrics": {"t": v}} for v in (2.0, 2.0)] +
                           [{"arm": "B", "metrics": {"t": v}} for v in (1.0, 1.0)]}
        self.assertIn("-50.0%", bench.report(receipt)[0])


class MicroCalibrationTests(unittest.TestCase):
    def test_fast_functions_get_a_loop(self):
        r = micro.compare(lambda: 1 + 1, lambda: 2, pairs=1, aa_pairs=0, min_sample_s=0.01)
        self.assertGreater(r["inner"], 1)
        self.assertTrue(r["exact"])

    def test_exactness_uses_fresh_inputs(self):
        def in_place(xs):
            xs.reverse()          # modifies its input
            return list(xs)
        r = micro.compare(in_place, lambda xs: list(reversed(xs)), make_input=lambda: [1, 2, 3], pairs=1,
                          aa_pairs=0, inner=1)
        self.assertTrue(r["exact"])


class RegistryExemptTests(unittest.TestCase):
    def test_exempt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.py").write_text("x['cache_a']; x['cache_keyed_elsewhere']\n", encoding="utf-8")
            (root / "C.md").write_text("| `cache_a` | why |\n", encoding="utf-8")
            cfg = {"doc": "C.md", "code": ["*.py"], "pattern": r"['\"](cache_[a-z_]+)['\"]",
                   "row": r"^\| `([A-Za-z0-9_.]+)` \|", "exempt": ["cache_keyed_elsewhere"]}
            self.assertEqual(registry.check(root, cfg), {"undocumented": [], "stale": []})


if __name__ == "__main__":
    unittest.main()

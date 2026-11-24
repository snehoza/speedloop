"""Tests for memory, parallel runs, the lock, the quiet check, budgets/sheets, micro, compare, init, profile."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from speedloop import bench, cli, exact, machine, micro, noise, proc

PY = sys.executable
ALLOC = "b = bytearray(200 * 2**20); b[::4096] = b'x' * len(b[::4096]); print('SPEEDLOOP_METRIC work=1')"


def quiet(**kw):
    return dict(progress=lambda *_: None, lock=False, quiet_threshold=None, **kw)


class MemoryAndParallelTests(unittest.TestCase):
    def test_peak_memory_is_recorded(self):
        m = proc.measure([PY, "-c", ALLOC])
        self.assertIn("peak_rss_mib", m)
        self.assertGreater(m["peak_rss_mib"], 150)   # the child touched 200 MiB

    def test_parallel_copies(self):
        m = proc.measure([PY, "-c", "import time; time.sleep(0.3)"], parallel=3)
        self.assertLess(m["wall_s"], 0.3 * 3)        # ran at the same time, not one after another
        self.assertGreater(m["peak_rss_mib_total"], m["peak_rss_mib"])

    def test_failure_and_timeout(self):
        with self.assertRaises(proc.CommandFailed):
            proc.measure([PY, "-c", "raise SystemExit(3)"])
        with self.assertRaises(subprocess.TimeoutExpired):
            proc.measure([PY, "-c", "import time; time.sleep(5)"], timeout=0.5)

    def test_bench_reports_memory(self):
        r = bench.run([PY, "-c", ALLOC], [PY, "-c", "print('SPEEDLOOP_METRIC work=1')"], pairs=1, aa_pairs=1,
                      warmup=0, **quiet())
        table, verdicts = bench.report(r)
        self.assertIn("peak_rss_mib", table)
        mem = [v for v in verdicts if v.base_median > 150][0]
        self.assertEqual(mem.verdict, noise.FASTER)   # less memory counts as better


class MachineTests(unittest.TestCase):
    def test_lock_excludes_a_second_holder(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "heavy.lock")
            holder = subprocess.Popen([PY, "-c", "import sys, time; sys.path.insert(0, sys.argv[2]);"
                                       "from speedloop.machine import HeavyLock;"
                                       "l = HeavyLock(sys.argv[1]); l.acquire(); print('held', flush=True);"
                                       "time.sleep(1.5); l.release()", str(path), str(Path(__file__).parents[1])],
                                      stdout=subprocess.PIPE, text=True)
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(machine.LockTimeout):
                machine.HeavyLock(path, timeout=0.3).acquire()
            with machine.HeavyLock(path, timeout=10):   # free again once the holder exits
                pass
            holder.wait()
            holder.stdout.close()

    def test_cpu_busy(self):
        busy = machine.cpu_busy(0.2)
        if busy is not None:
            self.assertGreaterEqual(busy, 0)
            self.assertLessEqual(busy, 100.5)
        self.assertIsNotNone(machine.wait_until_quiet(101, 1, 0.1))


class BudgetTests(unittest.TestCase):
    def test_budget_levels(self):
        st = lambda m: {"array": {"changed": 1, "of": 10, "max_abs": m, "p99_9_abs": 0}}  # noqa: E731
        self.assertEqual(exact.budget(st(0)), "identical")
        self.assertEqual(exact.budget(st(1)), "adopt")
        self.assertEqual(exact.budget(st(3)), "ask")
        self.assertEqual(exact.budget(st(9)), "reject")
        self.assertEqual(exact.budget({"array": {"shape_mismatch": True}}), "reject")
        self.assertEqual(exact.budget(None), "ask")

    def test_image_check_and_sheet(self):
        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            self.skipTest("numpy + Pillow not installed")
        with tempfile.TemporaryDirectory() as d:
            out, gold = Path(d, "out"), Path(d, "gold")
            out.mkdir()
            img = np.full((20, 30, 3), 100, np.uint8)
            Image.fromarray(img).save(out / "a.png")
            exact.build(out, gold)
            img[5, 5] = 101                                  # one pixel, one level
            Image.fromarray(img).save(out / "a.png")
            self.assertEqual(exact.check(out, gold)["verdict"], "adopt")
            img[6, 6] = 110
            Image.fromarray(img).save(out / "a.png")
            self.assertEqual(exact.check(out, gold)["verdict"], "reject")
            sheet = exact.sheet(gold / "a.png", out / "a.png", Path(d, "s.png"))
            with Image.open(sheet) as s:
                self.assertEqual(s.size, (30 * 3 + 16, 20))   # full size, three panels
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["golden", "check", str(out), str(gold)]), 1)
                self.assertEqual(cli.main(["golden", "sheet", str(out), str(gold), "--out", str(Path(d, "sheets"))]), 0)


class MicroTests(unittest.TestCase):
    def test_exact_and_faster(self):
        data = list(range(20000))
        r = micro.compare(lambda xs: sorted(xs, key=lambda v: -v)[::-1], lambda xs: sorted(xs), make_input=lambda: data,
                          pairs=3)
        self.assertTrue(r["exact"])
        self.assertIn("Exact", micro.report(r))
        self.assertEqual(len(r["runs"]), 16)

    def test_not_exact(self):
        r = micro.compare(lambda: [1, 2], lambda: [1, 3], pairs=1)
        self.assertFalse(r["exact"])
        self.assertIn("NOT EXACT", micro.report(r))

    def test_numpy_equal(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        a = np.arange(5, dtype=np.float32)
        self.assertTrue(micro.equal(a, a.copy()))
        self.assertFalse(micro.equal(a, a.astype(np.float64)))   # dtype changes count
        self.assertEqual(micro.max_abs_diff(a, a + 0.5), 0.5)


class CompareInitProfileLockedTests(unittest.TestCase):
    def test_cross_receipt(self):
        old = {"runs": [{"arm": "A", "metrics": {"t": v}} for v in (2.0, 2.1)] +
                       [{"arm": "A2", "metrics": {"t": v}} for v in (2.05, 2.0)], "env": {"platform": "x"}}
        new = {"runs": [{"arm": "B", "metrics": {"t": v}} for v in (1.0, 1.1)], "env": {"platform": "x"}}
        text, (v,) = bench.compare_receipts(old, new)
        self.assertEqual(v.verdict, noise.FASTER)
        self.assertIn("not interleaved", text)

    def test_init_profile_locked(self):
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(cli.main(["init", str(Path(d, "speed"))]), 0)
            self.assertEqual(sorted(p.name for p in Path(d, "speed").iterdir()), sorted(cli.TEMPLATES))
            script = Path(d, "s.py")
            script.write_text("def f():\n    return sum(range(10**5))\nf()\n", encoding="utf-8")
            self.assertEqual(cli.main(["profile", "--out", str(Path(d, "p.prof")), str(script)]), 0)
            self.assertIn("cumulative", buf.getvalue())
            lock = Path(d, "l.lock")
            import os
            os.environ["SPEEDLOOP_LOCK"] = str(lock)
            try:
                self.assertEqual(cli.main(["locked", "--", PY, "-c", "raise SystemExit(0)"]), 0)
            finally:
                del os.environ["SPEEDLOOP_LOCK"]


if __name__ == "__main__":
    unittest.main()

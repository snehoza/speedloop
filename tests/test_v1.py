"""Tests for memory, parallel runs, the lock, the quiet check, output tolerance and review, micro, compare, init, profile."""
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


class ToleranceTests(unittest.TestCase):
    def _pair(self, d, name, golden_text, new_text):
        a, b = Path(d, "g_" + name), Path(d, "n_" + name)
        a.write_text(golden_text, encoding="utf-8")
        b.write_text(new_text, encoding="utf-8")
        return a, b

    def test_levels(self):
        r = lambda m: {"kind": "json", "changed": 1, "max_abs": m, "max_rel": m / 10,  # noqa: E731
                       "non_numeric_changed": 0, "structure_changed": False}
        self.assertEqual(exact.tolerance(r(0.5)), "adopt")
        self.assertEqual(exact.tolerance(r(3)), "ask")
        self.assertEqual(exact.tolerance(r(9)), "reject")
        self.assertEqual(exact.tolerance(r(9), adopt_max=1, relative=True), "adopt")   # 9/10 relative

    def test_json(self):
        with tempfile.TemporaryDirectory() as d:
            same = exact.compare_files(*self._pair(d, "a.json", '{"x": 1.0, "y": [1, 2]}', '{"y":[1,2],"x":1}'))
            self.assertEqual(exact.tolerance(same), "identical")          # formatting and key order only
            small = exact.compare_files(*self._pair(d, "b.json", '{"t": 100.0}', '{"t": 100.4}'))
            self.assertEqual((small["changed"], exact.tolerance(small)), (1, "adopt"))
            label = exact.compare_files(*self._pair(d, "c.json", '{"k": "cat"}', '{"k": "dog"}'))
            self.assertEqual(exact.tolerance(label), "reject")            # a changed label is a real change
            keys = exact.compare_files(*self._pair(d, "e.json", '{"a": 1}', '{"a": 1, "b": 2}'))
            self.assertEqual(exact.tolerance(keys), "reject")

    def test_table_text_binary(self):
        with tempfile.TemporaryDirectory() as d:
            t = exact.compare_files(*self._pair(d, "a.csv", "id,price\n1,10.00\n2,5.5\n", "id,price\n1,10\n2,5.6\n"))
            self.assertEqual((t["kind"], t["changed"], exact.tolerance(t)), ("table", 1, "adopt"))
            # relative: 0.1 / 5.5 = 1.8 %, above a 1 % adopt limit and below a 3 % ask limit
            self.assertEqual(exact.tolerance(t, adopt_max=0.01, ask_max=0.03, relative=True), "ask")
            rows = exact.compare_files(*self._pair(d, "b.csv", "a\n1\n", "a\n1\n2\n"))
            self.assertEqual(exact.tolerance(rows), "reject")
            txt = exact.compare_files(*self._pair(d, "c.log", "one\ntwo\nthree\n", "one\n2\nthree\n"))
            self.assertEqual((txt["kind"], txt["lines_changed"], exact.tolerance(txt)), ("text", 1, "ask"))
            ga, gb = Path(d, "x.bin"), Path(d, "y.bin")
            ga.write_bytes(bytes([0xFF, 0xFE, 0x00, 0x81]))
            gb.write_bytes(bytes([0xFF, 0xFE, 0x00, 0x82]))
            self.assertEqual(exact.compare_files(ga, gb)["kind"], "binary")

    def test_review_files_for_text(self):
        with tempfile.TemporaryDirectory() as d:
            out, gold = Path(d, "out"), Path(d, "gold")
            out.mkdir()
            (out / "report.json").write_text('{"total": 10}', encoding="utf-8")
            exact.build(out, gold)
            (out / "report.json").write_text('{"total": 10.5}', encoding="utf-8")
            res = exact.check(out, gold)
            self.assertEqual(res["verdict"], "adopt")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["golden", "check", str(out), str(gold)]), 0)
                self.assertEqual(cli.main(["golden", "check", str(out), str(gold), "--strict"]), 1)
                cli.main(["golden", "review", str(out), str(gold), "--out", str(Path(d, "rev"))])
            self.assertIn("10.5", Path(d, "rev", "report.json.review.diff").read_text(encoding="utf-8"))

    def test_image_check_and_review(self):
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
            img[5, 5] = 101
            Image.fromarray(img).save(out / "a.png")
            self.assertEqual(exact.check(out, gold)["verdict"], "adopt")
            img[6, 6] = 110
            Image.fromarray(img).save(out / "a.png")
            self.assertEqual(exact.check(out, gold)["verdict"], "reject")
            sheet = exact.review(gold / "a.png", out / "a.png", Path(d, "rev"))
            with Image.open(sheet) as s:
                self.assertEqual(s.size, (30 * 3 + 16, 20))   # full size, three panels


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

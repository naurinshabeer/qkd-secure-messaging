from __future__ import annotations

import csv
import importlib.util
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.plot_results import make_plots
from experiments.run_sweeps import main as run_sweeps_main


class ExperimentScriptTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("matplotlib"), "install optional matplotlib to run plot check")
    def test_quick_sweeps_write_csvs_and_plot(self):
        def fake_run(settings, *, seed):
            qber = min(0.2, settings.channel_noise + settings.eve_fraction * 0.1 + settings.distance_km / 1000)
            keys = 1 if qber < 0.11 else 0
            return {"qber": qber, "accepted": bool(keys), "matching_cascade_keys": keys,
                    "signals_sent": 100, "signals_intercepted": 0, "qber_samples": 50,
                    "cascade_keys_generated": keys, "key_materials": [b"k" * 32] * keys}

        out = Path.cwd() / "experiments" / ".test-output"
        shutil.rmtree(out, ignore_errors=True)
        try:
            with patch("experiments.run_sweeps.run_sequence_bb84", side_effect=fake_run), \
                    patch("sys.argv", ["run_sweeps.py", "--quick", "--out", str(out)]):
                run_sweeps_main()
            raw_path, summary_path = out / "runs.csv", out / "summary.csv"
            with raw_path.open(newline="", encoding="utf-8") as stream:
                raw = list(csv.DictReader(stream))
            with summary_path.open(newline="", encoding="utf-8") as stream:
                summary = list(csv.DictReader(stream))
            self.assertEqual(len(raw), 6)
            self.assertEqual(len(summary), 6)
            self.assertTrue(all(row["raw_key_length_bits"] == "" for row in raw))
            self.assertTrue(all(0.0 <= float(row["acceptance_rate"]) <= 1.0 for row in summary))
            plots = make_plots(out, out)
            self.assertEqual(len(plots), 7)
            self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in plots))
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

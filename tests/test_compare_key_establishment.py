import csv
import tempfile
import unittest
from pathlib import Path

from experiments.compare_key_establishment import run_comparison


class CompareKeyEstablishmentTests(unittest.TestCase):
    def test_quick_comparison_writes_valid_csv_and_png(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root / "experiments") as temporary:
            csv_path, png_path = run_comparison(
                repetitions=2,
                result_dir=root / "experiments" / "results",
                output_dir=Path(temporary),
            )
            with csv_path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(rows)
            self.assertTrue(any(row["method"] == "X25519 + HKDF (both sides)" for row in rows))
            self.assertTrue(any(row["parameter"] == "eve_fraction" and row["runs"] == "2" for row in rows))
            self.assertTrue(any(row["method"] == "Unauthenticated X25519 MITM" for row in rows))
            self.assertGreater(png_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()

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
            mitm_rows = {row["method"]: row for row in rows if row["category"] == "attacker_demo"}
            unauthenticated = mitm_rows["Unauthenticated X25519 MITM"]
            self.assertEqual(unauthenticated["alice_matches_bob"], "False")
            self.assertEqual(unauthenticated["alice_matches_eve"], "True")
            self.assertEqual(unauthenticated["bob_matches_eve"], "True")
            self.assertIn("no detection", unauthenticated["handshake_result"])
            signed = mitm_rows["Ed25519-signed X25519 MITM"]
            self.assertEqual(signed["handshake_result"], "aborted")
            self.assertIn("FAILED", signed["signature_verification"])
            for field in ("platform", "python_version", "cryptography_version",
                          "cpu_count", "repetitions", "generated_utc"):
                self.assertTrue(unauthenticated[field], field)
            self.assertEqual(unauthenticated["repetitions"], "2")
            self.assertTrue(unauthenticated["generated_utc"].endswith("Z"))
            self.assertGreater(png_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()

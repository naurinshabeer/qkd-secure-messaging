"""Run one-factor-at-a-time SeQUeNCe BB84 sweeps and write CSV results."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

# Executing ``python experiments/run_sweeps.py`` puts only experiments/ on
# sys.path; include the repository root so the existing package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qkd_demo.coordinator import QBER_LIMIT
from qkd_demo.sequence_link import LinkSettings, run_sequence_bb84


PARAMETERS: dict[str, tuple[str, tuple[float, ...]]] = {
    "distance": ("distance_km", (1, 5, 10, 20, 30, 40, 50)),
    "noise": ("channel_noise", (0, 0.01, 0.02, 0.05, 0.08, 0.11, 0.15)),
    "eve": ("eve_fraction", (0, 0.1, 0.25, 0.5, 0.75, 1.0)),
}
KEY_BITS = 256
RUN_COLUMNS = [
    "sweep", "parameter", "parameter_value", "seed", "qber", "accepted",
    "raw_key_length_bits", "sifted_key_length_bits", "final_key_length_bits",
    "accepted_key_yield_bits", "run_time_s", "signals_sent", "signals_intercepted",
    "qber_samples", "cascade_keys_generated", "matching_cascade_keys",
]
SUMMARY_COLUMNS = [
    "sweep", "parameter", "parameter_value", "runs", "acceptance_rate",
    "qber_mean", "qber_std", "qber_ci95_low", "qber_ci95_high",
    "accepted_key_yield_bits_mean", "accepted_key_yield_bits_std",
    "accepted_key_yield_bits_ci95_low", "accepted_key_yield_bits_ci95_high",
    "key_rate_bits_per_second_mean", "key_rate_bits_per_second_std",
    "key_rate_bits_per_second_ci95_low", "key_rate_bits_per_second_ci95_high",
    "run_time_s_mean",
]


def _mean_std_ci(values: list[float]) -> tuple[float, float, float, float]:
    mean = statistics.mean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    margin = 1.96 * deviation / math.sqrt(len(values))
    return mean, deviation, max(0.0, mean - margin), mean + margin


def run_sweeps(seeds: int, out_dir: Path, *, quick: bool = False) -> tuple[Path, Path]:
    if seeds < 1:
        raise ValueError("--seeds must be at least 1.")
    out_dir.mkdir(parents=True, exist_ok=True)
    seed_values = range(1, min(seeds, 1) + 1) if quick else range(1, seeds + 1)
    point_limit = 2 if quick else None
    runs: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    defaults = LinkSettings()
    for sweep, (parameter, values) in PARAMETERS.items():
        selected_values = values[:point_limit] if point_limit else values
        for value in selected_values:
            observations: list[dict[str, Any]] = []
            for seed in seed_values:
                settings = LinkSettings(
                    distance_km=value if parameter == "distance_km" else defaults.distance_km,
                    channel_noise=value if parameter == "channel_noise" else defaults.channel_noise,
                    eve_fraction=value if parameter == "eve_fraction" else defaults.eve_fraction,
                )
                started = time.perf_counter()
                result = run_sequence_bb84(settings, seed=seed)
                elapsed = time.perf_counter() - started
                matching_count = int(result.get("matching_cascade_keys", 0))
                accepted = bool(result.get("accepted", False) and float(result["qber"]) < QBER_LIMIT)
                final_bits = matching_count * KEY_BITS
                row = {
                    "sweep": sweep,
                    "parameter": parameter,
                    "parameter_value": value,
                    "seed": seed,
                    "qber": float(result["qber"]),
                    "accepted": accepted,
                    # The current simulator does not expose raw or sifted bit counts.
                    "raw_key_length_bits": "",
                    "sifted_key_length_bits": "",
                    "final_key_length_bits": final_bits,
                    "accepted_key_yield_bits": final_bits if accepted else 0,
                    "run_time_s": elapsed,
                    "signals_sent": int(result.get("signals_sent", 0)),
                    "signals_intercepted": int(result.get("signals_intercepted", 0)),
                    "qber_samples": int(result.get("qber_samples", 0)),
                    "cascade_keys_generated": int(result.get("cascade_keys_generated", 0)),
                    "matching_cascade_keys": matching_count,
                }
                observations.append(row)
                runs.append(row)

            qber_stats = _mean_std_ci([row["qber"] for row in observations])
            yield_stats = _mean_std_ci([row["accepted_key_yield_bits"] for row in observations])
            rate_stats = _mean_std_ci([
                row["accepted_key_yield_bits"] / row["run_time_s"] if row["run_time_s"] else 0.0
                for row in observations
            ])
            summaries.append({
                "sweep": sweep,
                "parameter": parameter,
                "parameter_value": value,
                "runs": len(observations),
                "acceptance_rate": statistics.mean(float(row["accepted"]) for row in observations),
                "qber_mean": qber_stats[0], "qber_std": qber_stats[1],
                "qber_ci95_low": qber_stats[2], "qber_ci95_high": qber_stats[3],
                "accepted_key_yield_bits_mean": yield_stats[0],
                "accepted_key_yield_bits_std": yield_stats[1],
                "accepted_key_yield_bits_ci95_low": yield_stats[2],
                "accepted_key_yield_bits_ci95_high": yield_stats[3],
                "key_rate_bits_per_second_mean": rate_stats[0],
                "key_rate_bits_per_second_std": rate_stats[1],
                "key_rate_bits_per_second_ci95_low": rate_stats[2],
                "key_rate_bits_per_second_ci95_high": rate_stats[3],
                "run_time_s_mean": statistics.mean(row["run_time_s"] for row in observations),
            })

    raw_path, summary_path = out_dir / "runs.csv", out_dir / "summary.csv"
    _write_csv(raw_path, RUN_COLUMNS, runs)
    _write_csv(summary_path, SUMMARY_COLUMNS, summaries)
    return raw_path, summary_path


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=20, help="Number of deterministic simulator seeds per sweep point (default: 20).")
    parser.add_argument("--out", type=Path, default=Path("experiments/results"), help="Output directory (default: experiments/results).")
    parser.add_argument("--quick", action="store_true", help="Tiny smoke run: first two values of each sweep, one seed each.")
    args = parser.parse_args()
    try:
        raw, summary = run_sweeps(args.seeds, args.out, quick=args.quick)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"Wrote {raw} and {summary}")


if __name__ == "__main__":
    main()

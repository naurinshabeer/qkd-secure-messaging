"""Create experiment plots from run_sweeps.py summary CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

QBER_LIMIT = 0.11


def make_plots(input_dir: Path, output_dir: Path) -> list[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Plotting requires optional matplotlib; install requirements-experiments.txt") from exc

    summary_path = input_dir / "summary.csv"
    with summary_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    required = {"sweep", "parameter_value", "qber_mean", "qber_std", "qber_ci95_low",
                "qber_ci95_high", "accepted_key_yield_bits_mean",
                "accepted_key_yield_bits_ci95_low", "accepted_key_yield_bits_ci95_high",
                "key_rate_bits_per_second_mean", "key_rate_bits_per_second_ci95_low",
                "key_rate_bits_per_second_ci95_high"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{summary_path} is empty or missing required summary columns.")
    output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "axes.titlesize": 12, "axes.labelsize": 10,
                         "figure.dpi": 120, "savefig.dpi": 160})
    outputs: list[Path] = []

    specs = [
        ("distance", "qber_mean", "qber_std", "Distance (km)", "QBER (fraction)", "QBER vs distance", "qber_vs_distance.png", True),
        ("noise", "qber_mean", "qber_std", "Channel noise (fraction)", "QBER (fraction)", "QBER vs channel noise", "qber_vs_noise.png", True),
        ("eve", "qber_mean", "qber_std", "Eve intercept fraction", "QBER (fraction)", "QBER vs Eve intercept fraction", "qber_vs_eve.png", True),
        ("distance", "accepted_key_yield_bits_mean", "accepted_key_yield_bits_ci95_low", "Distance (km)", "Accepted final key yield (bits/run)", "Accepted-key yield vs distance", "accepted_key_yield_vs_distance.png", False),
        ("noise", "accepted_key_yield_bits_mean", "accepted_key_yield_bits_ci95_low", "Channel noise (fraction)", "Accepted final key yield (bits/run)", "Accepted-key yield vs channel noise", "accepted_key_yield_vs_noise.png", False),
        ("eve", "accepted_key_yield_bits_mean", "accepted_key_yield_bits_ci95_low", "Eve intercept fraction", "Accepted final key yield (bits/run)", "Accepted-key yield vs Eve intercept fraction", "accepted_key_yield_vs_eve.png", False),
        ("distance", "key_rate_bits_per_second_mean", "key_rate_bits_per_second_ci95_low", "Distance (km)", "Accepted final-key rate (bits/s)", "Accepted key rate vs distance", "key_rate_vs_distance.png", False),
    ]
    for sweep, metric, error, xlabel, ylabel, title, filename, qber in specs:
        selected = sorted((row for row in rows if row["sweep"] == sweep), key=lambda row: float(row["parameter_value"]))
        if not selected:
            continue
        x = [float(row["parameter_value"]) for row in selected]
        y = [float(row[metric]) for row in selected]
        if qber:
            errors = [float(row[error]) for row in selected]
        else:
            low_col = ("accepted_key_yield_bits_ci95_low" if metric.startswith("accepted_key")
                       else "key_rate_bits_per_second_ci95_low")
            high_col = ("accepted_key_yield_bits_ci95_high" if metric.startswith("accepted_key")
                        else "key_rate_bits_per_second_ci95_high")
            errors = [[max(0.0, value - float(row[low_col])) for value, row in zip(y, selected)],
                      [max(0.0, float(row[high_col]) - value) for value, row in zip(y, selected)]]
        fig, ax = plt.subplots(figsize=(6.4, 4.2), constrained_layout=True)
        ax.errorbar(x, y, yerr=errors, marker="o", capsize=3, linewidth=1.5, color="#256b68")
        if qber:
            ax.axhline(QBER_LIMIT, linestyle="--", color="#a4493e", label=f"Acceptance cutoff ({QBER_LIMIT:.2f})")
            ax.legend(frameon=False)
        ax.set(title=title, xlabel=xlabel, ylabel=ylabel)
        ax.grid(True, alpha=0.22)
        destination = output_dir / filename
        fig.savefig(destination, bbox_inches="tight")
        plt.close(fig)
        outputs.append(destination)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("experiments/results"), help="Directory containing summary.csv.")
    parser.add_argument("--out", type=Path, help="Plot directory (defaults to --input).")
    args = parser.parse_args()
    try:
        paths = make_plots(args.input, args.out or args.input)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"Wrote {len(paths)} plot(s) to {args.out or args.input}")


if __name__ == "__main__":
    main()

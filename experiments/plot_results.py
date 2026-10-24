"""Create QBER, key-efficiency, inverse-efficiency and acceptance plots."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

QBER_LIMIT = 0.11
SWEEPS = {
    "distance": ("Distance (km)", "distance"),
    "noise": ("Channel noise (fraction)", "noise"),
    "eve": ("Eve intercept fraction", "Eve fraction"),
}


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
    required = {"sweep", "parameter_value", "qber_mean", "qber_std", "qber_ci95_low", "qber_ci95_high",
                "key_efficiency_bits_per_signal_mean", "key_efficiency_bits_per_signal_ci95_low",
                "key_efficiency_bits_per_signal_ci95_high", "signals_per_key_bit_mean",
                "signals_per_key_bit_ci95_low", "signals_per_key_bit_ci95_high", "acceptance_rate_mean",
                "acceptance_rate_ci95_low", "acceptance_rate_ci95_high"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{summary_path} is empty or missing required summary columns.")
    output_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "axes.titlesize": 12, "axes.labelsize": 10,
                         "figure.dpi": 120, "savefig.dpi": 160})
    outputs: list[Path] = []

    def draw(sweep: str, metric: str, low: str, high: str, ylabel: str, title: str,
             filename: str, *, qber: bool = False, percent: bool = False) -> None:
        selected = sorted((row for row in rows if row["sweep"] == sweep),
                          key=lambda row: float(row["parameter_value"]))
        # Inverse efficiency is undefined when no run at a point accepted key bits.
        selected = [row for row in selected if row[metric] != "" and row[low] != "" and row[high] != ""]
        if not selected:
            return
        x = [float(row["parameter_value"]) for row in selected]
        scale = 100.0 if percent else 1.0
        y = [float(row[metric]) * scale for row in selected]
        lower = [max(0.0, (float(row[metric]) - float(row[low])) * scale) for row in selected]
        upper = [max(0.0, (float(row[high]) - float(row[metric])) * scale) for row in selected]
        fig, ax = plt.subplots(figsize=(6.4, 4.2), constrained_layout=True)
        ax.errorbar(x, y, yerr=[lower, upper], marker="o", capsize=3, linewidth=1.5, color="#256b68")
        if qber:
            ax.axhline(QBER_LIMIT, linestyle="--", color="#a4493e", label=f"Acceptance cutoff ({QBER_LIMIT:.2f})")
            ax.legend(frameon=False)
        ax.set(title=title, xlabel=SWEEPS[sweep][0], ylabel=ylabel)
        ax.grid(True, alpha=0.22)
        destination = output_dir / filename
        fig.savefig(destination, bbox_inches="tight")
        plt.close(fig)
        outputs.append(destination)

    for sweep, (_, label) in SWEEPS.items():
        draw(sweep, "qber_mean", "qber_ci95_low", "qber_ci95_high", "QBER (fraction)",
             f"QBER vs {label}", f"qber_vs_{sweep}.png", qber=True)
        draw(sweep, "key_efficiency_bits_per_signal_mean", "key_efficiency_bits_per_signal_ci95_low",
             "key_efficiency_bits_per_signal_ci95_high", "Accepted final key bits per signal sent",
             f"Key efficiency vs {label}", f"key_efficiency_vs_{sweep}.png")
        draw(sweep, "signals_per_key_bit_mean", "signals_per_key_bit_ci95_low",
             "signals_per_key_bit_ci95_high", "Signals sent per accepted key bit",
             f"Signals needed per key bit vs {label}", f"signals_per_key_bit_vs_{sweep}.png")
        draw(sweep, "acceptance_rate_mean", "acceptance_rate_ci95_low", "acceptance_rate_ci95_high",
             "Accepted runs (%)", f"Acceptance rate vs {label}",
             f"acceptance_rate_vs_{sweep}.png", percent=True)
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

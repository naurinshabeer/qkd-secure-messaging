"""Run reproducible load, link-failure, relay-trust, and moving-Eve studies."""

from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.network_sim.simulator import (  # noqa: E402
    KEY_BITS, POLICIES, QBER_CUTOFF, TOPOLOGY_SEED, LinkModel,
    attacker_schedule, connectivity_fraction, generate_topologies, simulate,
)

DEFAULT_OUT = ROOT / "experiments" / "results" / "network_sim"
CSV_FIELDS = ["experiment", "topology", "policy", "x_name", "x_value", "seed", "metric",
              "value", "mean", "ci95_low", "ci95_high", "assumed_pulse_rate", "detail"]


class Results:
    def __init__(self, pulse_rate: float):
        self.pulse_rate = pulse_rate
        self.rows: list[dict[str, Any]] = []

    def add_samples(self, experiment: str, topology: str, policy: str, x_name: str,
                    x_value: object, metric: str, samples: list[tuple[int, float]],
                    detail: str = "") -> None:
        values = [float(value) for _, value in samples]
        for seed, value in samples:
            self.rows.append({
                "experiment": experiment, "topology": topology, "policy": policy,
                "x_name": x_name, "x_value": x_value, "seed": seed, "metric": metric,
                "value": value, "mean": "", "ci95_low": "", "ci95_high": "",
                "assumed_pulse_rate": self.pulse_rate, "detail": detail,
            })
        mean = statistics.mean(values) if values else 0.0
        std = statistics.stdev(values) if len(values) > 1 else 0.0
        half_width = 1.96 * std / math.sqrt(len(values)) if values else 0.0
        self.rows.append({
            "experiment": experiment, "topology": topology, "policy": policy,
            "x_name": x_name, "x_value": x_value, "seed": "summary", "metric": metric,
            "value": "", "mean": mean, "ci95_low": max(0.0, mean - half_width),
            "ci95_high": min(1.0, mean + half_width) if metric in {
                "served_fraction", "fraction_pairs_connected", "relay_exposure_share",
            } else mean + half_width,
            "assumed_pulse_rate": self.pulse_rate, "detail": detail,
        })

    def write(self, output: Path) -> None:
        output.mkdir(parents=True, exist_ok=True)
        path = output / "network_sim_results.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(self.rows)


def _record_topologies(results: Results, topologies: dict[str, Any], model: LinkModel) -> None:
    for topology in topologies.values():
        for metric, value in (("node_count", len(topology.nodes)),
                              ("link_count", len(topology.links)),
                              ("end_user_pair_count", len(topology.pairs))):
            results.rows.append({
                "experiment": "topology_catalog", "topology": topology.name,
                "policy": "none", "x_name": "topology", "x_value": topology.name,
                "seed": TOPOLOGY_SEED, "metric": metric, "value": value,
                "mean": "", "ci95_low": "", "ci95_high": "",
                "assumed_pulse_rate": results.pulse_rate,
                "detail": f"pairs={topology.pairs}",
            })
        for edge in topology.links:
            signals, qber, rate = model.link_parameters(edge.distance_km)
            detail = f"link={edge.link_id}; {edge.a}-{edge.b}"
            for metric, value in (("baseline_qber", qber),
                                  ("signals_per_accepted_key_bit", signals),
                                  ("rate_bits_per_s", rate)):
                results.rows.append({
                    "experiment": "link_model", "topology": topology.name,
                    "policy": "none", "x_name": "distance_km", "x_value": edge.distance_km,
                    "seed": TOPOLOGY_SEED, "metric": metric, "value": value,
                    "mean": "", "ci95_low": "", "ci95_high": "",
                    "assumed_pulse_rate": results.pulse_rate, "detail": detail,
                })


def _load_experiment(results: Results, model: LinkModel, mesh: Any, seeds: list[int],
                     rates: list[float], steps: int) -> None:
    for load in rates:
        for policy in POLICIES:
            samples = []
            for seed in seeds:
                output = simulate(mesh, model, policy, load, seed, steps=steps)
                samples.append((seed, output["served_fraction"]))
            results.add_samples("load_sweep", mesh.name, policy, "aggregate_requests_per_s",
                                load, "served_fraction", samples,
                                detail=f"{len(mesh.pairs)} pairs share aggregate arrivals")


def _failure_experiment(results: Results, model: LinkModel, mesh: Any, seeds: list[int],
                        failed_counts: list[int], steps: int) -> None:
    for count in failed_counts:
        connectivity = []
        failed_by_seed: dict[int, frozenset[str]] = {}
        for seed in seeds:
            rng = random.Random(TOPOLOGY_SEED + seed * 100 + count)
            disabled = frozenset(rng.sample([edge.link_id for edge in mesh.links], count))
            failed_by_seed[seed] = disabled
            connectivity.append((seed, connectivity_fraction(mesh, mesh.pairs, disabled)))
        results.add_samples("failure_sweep", mesh.name, "all_pairs", "random_links_removed",
                            count, "fraction_pairs_connected", connectivity,
                            detail="Same per-seed failed-link set shared by every routing policy")
        for policy in POLICIES:
            samples = []
            for seed in seeds:
                outcome = simulate(mesh, model, policy, 20.0, seed + 1000, steps=steps,
                                   disabled=failed_by_seed[seed])
                samples.append((seed, outcome["served_fraction"]))
            results.add_samples("failure_sweep", mesh.name, policy, "random_links_removed",
                                count, "served_fraction", samples,
                                detail="Aggregate load 20 requests/s; 32-key initial/capped pools")


def _trust_experiment(results: Results, model: LinkModel, mesh: Any, seeds: list[int], steps: int) -> None:
    for policy in POLICIES:
        by_node: dict[str, list[tuple[int, float]]] = {node: [] for node in mesh.nodes}
        for seed in seeds:
            outcome = simulate(mesh, model, policy, 25.0, seed + 2000, steps=steps)
            served = int(outcome["served"])
            visits = outcome["relay_visits"]
            for node in mesh.nodes:
                share = visits[node] / served if served else 0.0
                by_node[node].append((seed, share))
        for node, samples in by_node.items():
            results.add_samples("trust_exposure", mesh.name, policy, "relay_id", node,
                                "relay_exposure_share", samples,
                                detail="Share of delivered requests with this node as an intermediate relay; compromise exposes those end-to-end keys")


def _moving_experiment(results: Results, model: LinkModel, mesh: Any, seeds: list[int],
                       widths: list[int], epochs: int, steps_per_epoch: int) -> None:
    steps = epochs * steps_per_epoch
    for width in widths:
        schedule = attacker_schedule(mesh, width, seed=TOPOLOGY_SEED + width,
                                     epochs=epochs, steps_per_epoch=steps_per_epoch)
        for policy in POLICIES:
            served_samples: dict[int, list[tuple[int, float]]] = {t: [] for t in range(steps)}
            crossed_samples: list[tuple[int, float]] = []
            exposed_samples: list[tuple[int, float]] = []
            recovery_samples: list[tuple[int, float]] = []
            for seed in seeds:
                outcome = simulate(mesh, model, policy, 20.0, seed + 3000,
                                   steps=steps, attack_by_step=schedule, initial_pool_keys=8)
                for tick, (arrivals, served, crossed) in enumerate(outcome["step_stats"]):
                    served_samples[tick].append((seed, served / arrivals if arrivals else 0.0))
                crossed_samples.append((seed, float(outcome["requests_crossing_attacked_links"])))
                exposed_samples.append((seed, float(outcome["requests_exposed_to_eve"])))
                recovery = outcome["recovery_times_s"]
                recovery_samples.append((seed, statistics.mean(recovery) if recovery else 0.0))
            for tick, samples in served_samples.items():
                edge_list = ",".join(sorted(schedule[tick]))
                results.add_samples("moving_eve", mesh.name, policy, "time_step", tick,
                                    "served_fraction", samples,
                                    detail=f"attacker_link_ids={edge_list}; simultaneous_attacked_links={width}; epoch={tick // steps_per_epoch}; initial_pool_keys=8")
                attacked_qber = statistics.mean(
                    model.link_parameters(edge.distance_km, attacker_present=True)[1]
                    for edge in mesh.links if edge.link_id in schedule[tick]
                )
                results.add_samples("moving_eve", mesh.name, policy, "time_step", tick,
                                    "attacked_link_qber", [(seed, attacked_qber) for seed in seeds],
                                    detail=f"full intercept; edge IDs={edge_list}; cutoff={QBER_CUTOFF}")
            results.add_samples("moving_eve", mesh.name, policy, "simultaneous_attacked_links",
                                width, "requests_crossing_attacked_links", crossed_samples,
                                detail="Path crossed an attacked link while it had an older accepted pool key; not counted as cryptographic exposure")
            results.add_samples("moving_eve", mesh.name, policy, "simultaneous_attacked_links",
                                width, "requests_exposed_to_eve", exposed_samples,
                                detail="Zero by model: attacked links have QBER above cutoff, so no new accepted keys reveal information to Eve")
            results.add_samples("moving_eve", mesh.name, policy, "simultaneous_attacked_links",
                                width, "mean_recovery_time_s", recovery_samples,
                                detail="Mean time after each link leaves the attack until its pool has at least one 256-bit key")


def _summary_values(rows: list[dict[str, Any]], experiment: str, *, metric: str | None = None,
                    policy: str | None = None, x_name: str | None = None) -> list[dict[str, Any]]:
    return [row for row in rows if row["experiment"] == experiment and row["seed"] == "summary"
            and (metric is None or row["metric"] == metric)
            and (policy is None or row["policy"] == policy)
            and (x_name is None or row["x_name"] == x_name)]


def _plot(results: Results, output_dir: Path, *, steps_per_epoch: int, epochs: int) -> Path:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Network simulation plots require optional matplotlib from requirements-experiments.txt") from exc
    fig, axes = plt.subplots(3, 2, figsize=(15, 14), constrained_layout=True)
    load_ax, failure_ax, trust_ax, *moving_axes = axes.flatten()
    for policy in POLICIES:
        rows = sorted(_summary_values(results.rows, "load_sweep", metric="served_fraction", policy=policy),
                      key=lambda row: float(row["x_value"]))
        x = [float(row["x_value"]) for row in rows]
        means = [float(row["mean"]) for row in rows]
        low = [mean - float(row["ci95_low"]) for mean, row in zip(means, rows)]
        high = [float(row["ci95_high"]) - mean for mean, row in zip(means, rows)]
        load_ax.errorbar(x, means, yerr=[low, high], marker="o", capsize=3, label=policy)
    load_ax.set_title("Load sweep · metro mesh")
    load_ax.set_xlabel("Aggregate requests per second (4 user pairs)")
    load_ax.set_ylabel("Fraction served")
    load_ax.set_ylim(-.03, 1.03)
    load_ax.grid(alpha=.2)
    load_ax.legend()

    connected_rows = sorted(_summary_values(results.rows, "failure_sweep", metric="fraction_pairs_connected"),
                            key=lambda row: int(row["x_value"]))
    fx = [int(row["x_value"]) for row in connected_rows]
    connected_means = [float(row["mean"]) for row in connected_rows]
    failure_ax.errorbar(fx, connected_means,
                        yerr=[[mean - float(row["ci95_low"]) for mean, row in zip(connected_means, connected_rows)],
                              [float(row["ci95_high"]) - mean for mean, row in zip(connected_means, connected_rows)]],
                        color="#555555", linestyle="--", marker="o", capsize=2, label="Pairs connected")
    for policy in POLICIES:
        rows = sorted(_summary_values(results.rows, "failure_sweep", metric="served_fraction", policy=policy),
                      key=lambda row: int(row["x_value"]))
        means = [float(row["mean"]) for row in rows]
        failure_ax.errorbar([int(row["x_value"]) for row in rows], means,
                            yerr=[[mean - float(row["ci95_low"]) for mean, row in zip(means, rows)],
                                  [float(row["ci95_high"]) - mean for mean, row in zip(means, rows)]],
                            marker=".", capsize=2, label=f"Served · {policy}")
    failure_ax.set_title("Random link failures · metro mesh")
    failure_ax.set_xlabel("Links removed")
    failure_ax.set_ylabel("Connected / served fraction")
    failure_ax.set_ylim(-.03, 1.03)
    failure_ax.grid(alpha=.2)
    failure_ax.legend(fontsize=7)

    nodes = sorted({row["x_value"] for row in _summary_values(results.rows, "trust_exposure")})
    positions = list(range(len(nodes)))
    width = .24
    for offset, policy in enumerate(POLICIES):
        rows = {row["x_value"]: row for row in _summary_values(results.rows, "trust_exposure",
                                                                 metric="relay_exposure_share", policy=policy)}
        means = [float(rows[node]["mean"]) for node in nodes]
        trust_ax.bar([pos + (offset - 1) * width for pos in positions], means, width,
                     yerr=[[mean - float(rows[node]["ci95_low"]) for mean, node in zip(means, nodes)],
                           [float(rows[node]["ci95_high"]) - mean for mean, node in zip(means, nodes)]],
                     capsize=1.5, label=policy)
    trust_ax.set_title("Request share exposed if each relay is compromised")
    trust_ax.set_xticks(positions, nodes, rotation=60)
    trust_ax.set_ylabel("Share of delivered requests")
    trust_ax.set_ylim(0, 1)
    trust_ax.grid(axis="y", alpha=.2)
    trust_ax.legend(fontsize=7)

    for axis, width_count in zip(moving_axes, (1, 2, 3)):
        for policy in POLICIES:
            rows = [row for row in _summary_values(results.rows, "moving_eve", metric="served_fraction", policy=policy)
                    if int(row["detail"].split("simultaneous_attacked_links=")[1].split(";")[0]) == width_count]
            rows.sort(key=lambda row: int(row["x_value"]))
            xs = [int(row["x_value"]) for row in rows]
            means = [float(row["mean"]) for row in rows]
            lows = [float(row["ci95_low"]) for row in rows]
            highs = [float(row["ci95_high"]) for row in rows]
            axis.plot(xs, means, marker=".", markersize=3, label=policy)
            axis.fill_between(xs, lows, highs, alpha=.12)
        for epoch in range(1, epochs):
            axis.axvline(epoch * steps_per_epoch - .5, color="#a74e45", linestyle=":", linewidth=.8)
        for epoch in range(epochs):
            tick = epoch * steps_per_epoch
            position_row = next(row for row in _summary_values(
                results.rows, "moving_eve", metric="served_fraction", policy=POLICIES[0])
                                if int(row["x_value"]) == tick
                                and int(row["detail"].split("simultaneous_attacked_links=")[1].split(";")[0]) == width_count)
            ids = position_row["detail"].split("attacker_link_ids=")[1].split(";")[0].split(",")
            first_link = ids[0].removeprefix("mesh-")
            label = first_link if width_count == 1 else f"{first_link} +{width_count - 1}"
            axis.text(tick + steps_per_epoch / 2, .91, f"Eve: {label}",
                      transform=axis.get_xaxis_transform(), ha="center", va="top", fontsize=5)
        axis.set_title(f"Moving Eve · {width_count} link(s) attacked at once")
        axis.set_xlabel("Simulation time step")
        axis.set_ylabel("Requests served in step")
        axis.set_ylim(-.03, 1.03)
        axis.grid(alpha=.2)
        axis.legend(fontsize=7)
    fig.suptitle(f"Trusted-relay network model · assumed pulse rate {results.pulse_rate:g} signals/s · 95% CI across seeds", fontsize=14)
    path = output_dir / "network_sim_results.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def run_experiments(output_dir: Path, *, seeds: int = 5, pulse_rate: float = 1e6,
                    quick: bool = False) -> tuple[Path, Path, Results]:
    if seeds < 1:
        raise ValueError("--seeds must be positive")
    if pulse_rate <= 0:
        raise ValueError("--pulse-rate must be positive")
    topologies = generate_topologies()
    summary_path = ROOT / "experiments" / "results" / "summary.csv"
    model = LinkModel(summary_path, pulse_rate)
    seeds_used = list(range(1, (1 if quick else seeds) + 1))
    load_values = [2.0, 50.0] if quick else [2.0, 10.0, 25.0, 50.0, 100.0, 200.0]
    fail_values = [0, 1] if quick else list(range(6))
    duration = 6 if quick else 60
    epochs = 2 if quick else 6
    steps_per_epoch = 2 if quick else 10
    results = Results(pulse_rate)
    _record_topologies(results, topologies, model)

    diamond = topologies["diamond"]
    for policy in POLICIES:
        samples = [(seed, simulate(diamond, model, policy, 1.0, seed, steps=duration)["served_fraction"])
                   for seed in seeds_used]
        results.add_samples("topology_validation", diamond.name, policy, "aggregate_requests_per_s",
                            1.0, "served_fraction", samples,
                            detail="Four-node project diamond validation topology")

    mesh = topologies["metro15"]
    _load_experiment(results, model, mesh, seeds_used, load_values, duration)
    _failure_experiment(results, model, mesh, seeds_used, fail_values, duration)
    _trust_experiment(results, model, mesh, seeds_used, duration)
    _moving_experiment(results, model, mesh, seeds_used, [1, 2, 3], epochs, steps_per_epoch)
    results.write(output_dir)
    png_path = _plot(results, output_dir, steps_per_epoch=steps_per_epoch, epochs=epochs)
    return output_dir / "network_sim_results.csv", png_path, results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=5, help="Independent demand seeds per point (default 5).")
    parser.add_argument("--pulse-rate", type=float, default=1e6,
                        help="ASSUMPTION: emitted signals per second per link (default 1e6).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--quick", action="store_true", help="Smoke run with one seed, two loads, and short timelines.")
    args = parser.parse_args()
    try:
        csv_path, png_path, _ = run_experiments(args.out, seeds=args.seeds,
                                                 pulse_rate=args.pulse_rate, quick=args.quick)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"Wrote {csv_path}")
    print(f"Wrote {png_path}")
    print(f"ASSUMPTION assumed_pulse_rate={args.pulse_rate:g} signals/s/link; independent seed count={1 if args.quick else args.seeds}")
    print(f"Generated topologies with fixed seed {TOPOLOGY_SEED}; key size={KEY_BITS} bits; QBER cutoff={QBER_CUTOFF:.0%}")


if __name__ == "__main__":
    main()

"""Measure application delivery and failover latency against a running local lab."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def request_json(base_url: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base_url.rstrip("/") + path, data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def percentile95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def edge(link_map: dict[frozenset[str], dict[str, Any]], left: str, right: str) -> dict[str, Any]:
    return link_map[frozenset((left, right))]


def ensure_keys(base_url: str, path: list[str]) -> None:
    for _ in range(2):
        state = request_json(base_url, "/api/state")
        current_links = {frozenset((item["a"], item["b"])): item for item in state["links"]}
        if all(edge(current_links, a, b)["pool_count"] > 0 for a, b in zip(path, path[1:])):
            return
        request_json(base_url, "/api/keys/generate", {"count": 24})
    raise RuntimeError("Selected path still has no matching link keys after provisioning; check SeQUeNCe acceptance/QBER.")


def send_timed(base_url: str, text: str) -> tuple[float, list[str]]:
    started = time.perf_counter()
    result = request_json(base_url, "/api/messages", {"text": text})
    elapsed_ms = (time.perf_counter() - started) * 1000
    if result.get("status") != "delivered":
        raise RuntimeError(f"Message was not delivered: {result}")
    return elapsed_ms, result.get("path", [])


def run_benchmark(base_url: str, topology_path: Path, output_dir: Path, message_count: int = 50,
                  followup_count: int = 10) -> tuple[Path, Path, Path]:
    if message_count < 1 or followup_count < 1:
        raise ValueError("Message counts must be positive.")
    try:
        state = request_json(base_url, "/api/state")
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise RuntimeError(f"Local network is not running at {base_url}: {exc}") from exc
    if not state.get("topology") or not state.get("paths"):
        raise RuntimeError("The running coordinator does not expose the trusted-relay topology.")
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    if not {"nodes", "links"}.issubset(topology):
        raise RuntimeError(f"Invalid topology configuration: {topology_path}")
    link_map = {frozenset((item["a"], item["b"])): item for item in state["links"]}
    routes = sorted(state["paths"], key=lambda path: sum(edge(link_map, a, b)["distance_km"]
                                                            for a, b in zip(path, path[1:])))
    normal_path = next((path for path in routes if all(edge(link_map, a, b)["up"]
                                                       for a, b in zip(path, path[1:]))), None)
    if normal_path is None:
        raise RuntimeError("No up Alice-to-Bob path is available for the normal-path run.")
    fail_link = edge(link_map, normal_path[0], normal_path[1])["id"]
    samples: list[dict[str, Any]] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        for index in range(1, message_count + 1):
            ensure_keys(base_url, normal_path)
            latency, used_path = send_timed(base_url, f"network benchmark normal {index}")
            if used_path != normal_path:
                raise RuntimeError(f"Expected normal route {normal_path}, got {used_path}; benchmark stopped.")
            samples.append({"group": "normal", "index": index, "latency_ms": latency,
                            "path": ">".join(used_path)})

        request_json(base_url, f"/api/links/{fail_link}/fail", {})
        try:
            for index in range(1, followup_count + 2):
                alternate_path = next(path for path in routes if fail_link not in
                                      [edge(link_map, a, b)["id"] for a, b in zip(path, path[1:])])
                ensure_keys(base_url, alternate_path)
                latency, used_path = send_timed(base_url, f"network benchmark failover {index}")
                if fail_link in [edge(link_map, a, b)["id"] for a, b in zip(used_path, used_path[1:])]:
                    raise RuntimeError("Coordinator did not fail over from the failed link.")
                group = "failover_first" if index == 1 else "failover_following"
                samples.append({"group": group, "index": index if index == 1 else index - 1,
                                "latency_ms": latency, "path": ">".join(used_path)})
        finally:
            request_json(base_url, f"/api/links/{fail_link}/restore", {})
    except (OSError, urllib.error.URLError, ValueError, KeyError) as exc:
        raise RuntimeError(f"Network benchmark could not complete: {exc}") from exc

    summaries = []
    for group in ("normal", "failover_first", "failover_following"):
        values = [float(row["latency_ms"]) for row in samples if row["group"] == group]
        summaries.append({"group": group, "n": len(values), "mean_ms": statistics.mean(values),
                          "median_ms": statistics.median(values), "p95_ms": percentile95(values)})
    samples_path, summary_path = output_dir / "network_latency_samples.csv", output_dir / "network_latency_summary.csv"
    _write_csv(samples_path, ["group", "index", "latency_ms", "path"], samples)
    _write_csv(summary_path, ["group", "n", "mean_ms", "median_ms", "p95_ms"], summaries)
    plot_path = _plot(samples_path, output_dir / "network_latency.png")
    return samples_path, summary_path, plot_path


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _plot(samples_path: Path, destination: Path) -> Path:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Plotting requires optional matplotlib; install requirements-experiments.txt") from exc
    with samples_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    groups = ("normal", "failover_first", "failover_following")
    values = [[float(row["latency_ms"]) for row in rows if row["group"] == group] for group in groups]
    fig, ax = plt.subplots(figsize=(7, 4.5), constrained_layout=True)
    labels = ("Normal path", "First after failure", "Next 10 after failure")
    try:
        ax.boxplot(values, tick_labels=labels, showfliers=False)
    except TypeError:  # matplotlib 3.8 uses the older keyword name
        ax.boxplot(values, labels=labels, showfliers=False)
    ax.set(title="Local network delivery latency", ylabel="End-to-end latency (ms)")
    ax.grid(axis="y", alpha=0.22)
    fig.savefig(destination, bbox_inches="tight")
    plt.close(fig)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765", help="Local coordinator/dashboard URL.")
    parser.add_argument("--topology", type=Path, default=Path("config/topology.json"))
    parser.add_argument("--messages", type=int, default=50, help="Normal-path message count (default: 50).")
    parser.add_argument("--out", type=Path, default=Path("experiments/results"))
    args = parser.parse_args()
    try:
        paths = run_benchmark(args.base_url, args.topology, args.out, args.messages)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    for path in paths:
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()

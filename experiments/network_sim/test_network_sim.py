from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from experiments.network_sim.simulator import (
    KEY_BITS, POOL_CAPACITY_BITS, Link, Topology, attacker_schedule,
    consume_path, find_path, generate_topologies, refill_pool,
)

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


class NetworkSimulatorTests(unittest.TestCase):
    def test_routing_policies_choose_expected_tiny_graph_paths(self):
        topology = Topology("tiny", ("a", "b", "c"), (
            Link("ab", "a", "b", 20), Link("ac", "a", "c", 2), Link("cb", "c", "b", 2),
        ), (("a", "b"),))
        self.assertEqual(find_path(topology, "a", "b", "fewest_hops"), ["a", "b"])
        self.assertEqual(find_path(topology, "a", "b", "shortest_distance"), ["a", "c", "b"])
        pools = {"ab": 0, "ac": KEY_BITS, "cb": KEY_BITS}
        self.assertEqual(find_path(topology, "a", "b", "key_aware", pools), ["a", "c", "b"])

    def test_refill_is_capped_and_path_consumption_is_atomic(self):
        current = refill_pool(0, 512, 1)
        self.assertEqual(current, 512)
        self.assertEqual(refill_pool(current, 10_000, 10), POOL_CAPACITY_BITS)
        topology = Topology("tiny", ("a", "b", "c"), (
            Link("ab", "a", "b", 5), Link("bc", "b", "c", 5),
        ), (("a", "c"),))
        links = {frozenset((edge.a, edge.b)): edge for edge in topology.links}
        pools = {"ab": KEY_BITS, "bc": 0}
        self.assertFalse(consume_path(pools, ["a", "b", "c"], links))
        self.assertEqual(pools, {"ab": KEY_BITS, "bc": 0})
        pools["bc"] = KEY_BITS
        self.assertTrue(consume_path(pools, ["a", "b", "c"], links))
        self.assertEqual(pools, {"ab": 0, "bc": 0})

    def test_attacker_moves_to_an_adjacent_link_at_each_epoch(self):
        topology = generate_topologies()["metro15"]
        schedule = attacker_schedule(topology, 1, seed=7, epochs=4, steps_per_epoch=3)
        self.assertEqual(set(schedule), set(range(12)))
        by_epoch = [schedule[index * 3] for index in range(4)]
        link_by_id = {edge.link_id: edge for edge in topology.links}
        self.assertTrue(all(len(active) == 1 for active in by_epoch))
        for current, following in zip(by_epoch, by_epoch[1:]):
            left, right = link_by_id[next(iter(current))], link_by_id[next(iter(following))]
            self.assertTrue({left.a, left.b} & {right.a, right.b})

    def test_quick_mode_writes_csv_and_png(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temporary:
            completed = subprocess.run(
                [sys.executable, "-m", "experiments.network_sim.run_experiments",
                 "--quick", "--out", temporary],
                cwd=ROOT, capture_output=True, text=True, check=False, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            csv_path = Path(temporary) / "network_sim_results.csv"
            png_path = Path(temporary) / "network_sim_results.png"
            with csv_path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(rows)
            self.assertTrue(all(row["assumed_pulse_rate"] == "1000000.0" for row in rows))
            self.assertGreater(png_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()

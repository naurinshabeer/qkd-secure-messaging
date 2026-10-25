import hashlib
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from qkd_demo.coordinator import CoordinatorError, CoordinatorHTTPServer, CoordinatorService
from qkd_demo.node import AppTCPServer, NodeHTTPServer, NodeRuntime
from qkd_demo.topology import load_topology
try:
    from support import TEST_TOKEN, free_port
except ModuleNotFoundError:
    from tests.support import TEST_TOKEN, free_port


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY_PATH = ROOT / "config" / "topology_6node.json"
os.environ.setdefault("QKD_PROVISION_TOKEN", TEST_TOKEN)


def deterministic_experiment(settings, *, seed=None):
    material = hashlib.sha256(str(settings.distance_km).encode("ascii")).digest()
    return {
        "simulator": "SeQUeNCe", "protocol": "BB84 + Cascade", "seed": seed or 17,
        "distance_km": settings.distance_km, "eve_fraction": settings.eve_fraction,
        "channel_noise": settings.channel_noise, "polarization_fidelity": .97,
        "signals_sent": 100, "signals_intercepted": 0, "qber_samples": 1,
        "qber": .02, "cascade_keys_generated": 1, "matching_cascade_keys": 1,
        "cascade_qber": 0, "cascade_throughput_bps": 1000,
        "cascade_privacy_throughput_bps": 900, "accepted": True,
        "key_materials": [material],
    }


class SixNodeTopologyTests(unittest.TestCase):
    def setUp(self):
        self.topology = load_topology(TOPOLOGY_PATH)
        used_ports = set()

        def unique_port():
            while True:
                port = free_port()
                if port not in used_ports:
                    used_ports.add(port)
                    return port

        for config in self.topology["nodes"].values():
            config["http_port"] = unique_port()
            config["app_port"] = unique_port()

        self.coord_port = unique_port()
        self.coordinator_url = f"http://127.0.0.1:{self.coord_port}"
        self.nodes = {}
        self.servers = []
        self.threads = []
        for node_id, config in self.topology["nodes"].items():
            runtime = NodeRuntime(
                config["role"], "127.0.0.1", config["http_port"], "127.0.0.1",
                config["app_port"], None, None, coordinator_url=self.coordinator_url,
                node_id=node_id, topology=self.topology,
            )
            http_server = NodeHTTPServer(("127.0.0.1", config["http_port"]), runtime)
            tcp_server = AppTCPServer(("127.0.0.1", config["app_port"]), runtime)
            runtime.http_port = http_server.server_port
            runtime.app_port = int(tcp_server.server_address[1])
            self.nodes[node_id] = runtime
            self.servers.extend((http_server, tcp_server))
            for server in (http_server, tcp_server):
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.threads.append(thread)

        self.service = CoordinatorService("", "", self.topology)
        self.coordinator_server = CoordinatorHTTPServer(("127.0.0.1", self.coord_port), self.service)
        self.servers.append(self.coordinator_server)
        thread = threading.Thread(target=self.coordinator_server.serve_forever, daemon=True)
        thread.start()
        self.threads.append(thread)

    def tearDown(self):
        for server in reversed(self.servers):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=1)

    def generate_keys(self):
        with patch("qkd_demo.coordinator.run_sequence_bb84", side_effect=deterministic_experiment):
            return self.service.generate(1)

    def assert_endpoint_pools_match(self):
        for link in self.topology["links"]:
            left = self.nodes[link["a"]].kme_for(link["b"])
            right = self.nodes[link["b"]].kme_for(link["a"])
            self.assertEqual(set(left.key_ids()), set(right.key_ids()), link["id"])
            for key_id in left.key_ids():
                self.assertEqual(left._keys[key_id].material, right._keys[key_id].material, link["id"])

    def test_coordinator_enumerates_six_alice_bob_paths(self):
        self.assertEqual(len(self.service.paths), 6)
        self.assertTrue(all(path[0] == "alice" and path[-1] == "bob" for path in self.service.paths))

    def test_key_generation_provisions_both_ends_of_all_eight_links(self):
        result = self.generate_keys()
        self.assertEqual(result["generated"], 8)
        self.assert_endpoint_pools_match()
        self.assertTrue(all(self.nodes[link["a"]].kme_for(link["b"]).total_count() > 0
                            for link in self.topology["links"]))

    def test_first_message_uses_shortest_fifteen_km_route(self):
        self.generate_keys()
        result = self.service.send_message("shortest route")
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["path"], ["alice", "r1", "bob"])

    def test_route_with_two_consecutive_relays_fails_over_and_keeps_pools_matched(self):
        self.generate_keys()
        self.service.set_link(False, "A-R1")
        result = self.service.send_message("two relays in sequence")
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["path"], ["alice", "r2", "r3", "bob"])
        self.assert_endpoint_pools_match()

    def test_failing_both_alice_links_returns_clear_no_route_error(self):
        self.generate_keys()
        self.service.set_link(False, "A-R1")
        self.service.set_link(False, "A-R2")
        with self.assertRaisesRegex(CoordinatorError, "Both Alice-to-Bob relay paths are unavailable"):
            self.service.send_message("no route")


if __name__ == "__main__":
    unittest.main()

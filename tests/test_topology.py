import unittest
from pathlib import Path

from qkd_demo.node import resolve_app_port
from qkd_demo.topology import TopologyError, load_topology, loads_topology, validate_topology


ROOT = Path(__file__).resolve().parents[1]


def minimal_topology():
    return {
        "nodes": {
            "alice": {"role": "alice", "http_port": 9001, "app_port": 9002},
            "bob": {"role": "bob", "http_port": 9003, "app_port": 9004},
        },
        "links": [{"id": "A-B", "a": "alice", "b": "bob", "distance_km": 2}],
    }


class TopologyValidationTests(unittest.TestCase):
    def test_both_shipped_topologies_validate(self):
        default = load_topology(ROOT / "config" / "topology.json")
        larger = load_topology(ROOT / "config" / "topology_6node.json")
        self.assertEqual((len(default["nodes"]), len(default["links"])), (4, 4))
        self.assertEqual((len(larger["nodes"]), len(larger["links"])), (6, 8))

    def test_duplicate_node_id_in_json_is_rejected(self):
        source = '{"nodes":{"alice":{"http_port":9001,"app_port":9002},"alice":{}},"links":[]}'
        with self.assertRaisesRegex(TopologyError, "Duplicate JSON object key 'alice'"):
            loads_topology(source)

    def test_duplicate_link_id_is_rejected(self):
        topology = minimal_topology()
        topology["links"].append(dict(topology["links"][0]))
        with self.assertRaisesRegex(TopologyError, "Duplicate link ID 'A-B'"):
            validate_topology(topology)

    def test_unknown_node_reference_is_rejected(self):
        topology = minimal_topology()
        topology["links"][0]["b"] = "r9"
        with self.assertRaisesRegex(TopologyError, "unknown node 'r9'"):
            validate_topology(topology)

    def test_http_app_and_cross_type_port_conflicts_are_rejected(self):
        cases = (("shared HTTP", lambda t: t["nodes"]["bob"].update(http_port=9001)),
                 ("shared app", lambda t: t["nodes"]["bob"].update(app_port=9002)),
                 ("app equals other HTTP", lambda t: t["nodes"]["bob"].update(app_port=9001)),
                 ("same-node HTTP and app", lambda t: t["nodes"]["alice"].update(app_port=9001)))
        for description, mutate in cases:
            with self.subTest(description=description):
                topology = minimal_topology()
                mutate(topology)
                with self.assertRaisesRegex(TopologyError, "Port 9001|Port 9002"):
                    validate_topology(topology)

    def test_ports_outside_allowed_range_are_rejected(self):
        for port in (1023, 65536):
            with self.subTest(port=port):
                topology = minimal_topology()
                topology["nodes"]["alice"]["http_port"] = port
                with self.assertRaisesRegex(TopologyError, "1024 through 65535"):
                    validate_topology(topology)

    def test_missing_alice_or_bob_is_rejected(self):
        for missing in ("alice", "bob"):
            with self.subTest(missing=missing):
                topology = minimal_topology()
                del topology["nodes"][missing]
                with self.assertRaisesRegex(TopologyError, f"must define node '{missing}'"):
                    validate_topology(topology)

    def test_disconnected_alice_and_bob_are_rejected(self):
        topology = minimal_topology()
        topology["links"] = []
        with self.assertRaisesRegex(TopologyError, "Alice and Bob are not connected"):
            validate_topology(topology)

    def test_node_app_port_defaults_to_topology_then_cli_then_legacy(self):
        topology = {"nodes": {"r3": {"http_port": 9100, "app_port": 9137}}}
        self.assertEqual(resolve_app_port(topology, "r3", 9100), 9137)
        self.assertEqual(resolve_app_port(topology, "r3", 9100, 9199), 9199)
        self.assertEqual(resolve_app_port(None, "alice", 8766), 8768)

if __name__ == "__main__":
    unittest.main()

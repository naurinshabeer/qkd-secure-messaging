import unittest

from experiments.benchmark_network import require_default_topology as require_benchmark_topology
from experiments.moving_eve import require_default_topology as require_moving_eve_topology


class ExperimentTopologyGuardTests(unittest.TestCase):
    def test_default_link_ids_are_allowed(self):
        state = {"topology": True, "links": [
            {"id": link_id} for link_id in ("A-R1", "R1-B", "A-R2", "R2-B")
        ]}
        require_moving_eve_topology(state)
        require_benchmark_topology(state)

    def test_six_node_state_is_rejected_with_script_specific_message(self):
        state = {"topology": True, "links": [{"id": f"link-{index}"} for index in range(8)]}
        for guard, script in ((require_moving_eve_topology, "moving_eve.py"),
                              (require_benchmark_topology, "benchmark_network.py")):
            with self.subTest(script=script), self.assertRaisesRegex(
                    RuntimeError, f"experiments/{script} supports only config/topology.json"):
                guard(state)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from qkd_demo.engine import QKDService
from qkd_demo.sequence_link import LinkSettings, run_sequence_bb84


def fake_experiment(settings, *, seed=None):
    return {
        "simulator": "SeQUeNCe", "protocol": "BB84 + Cascade", "seed": seed or 17,
        "distance_km": settings.distance_km, "eve_fraction": settings.eve_fraction,
        "channel_noise": settings.channel_noise, "polarization_fidelity": 0.97,
        "signals_sent": 100_000, "signals_intercepted": 0, "qber_samples": 2,
        "qber": 0.02, "cascade_keys_generated": 40, "matching_cascade_keys": 40,
        "cascade_qber": 0.0, "cascade_throughput_bps": 1000,
        "cascade_privacy_throughput_bps": 900, "accepted": True,
        "key_materials": [bytes([i]) * 32 for i in range(40)],
    }


class SeQUeNCeLinkTests(unittest.TestCase):
    def test_clean_link_runs_bb84_and_cascade(self):
        result = run_sequence_bb84(LinkSettings(10, 0, 0.01), seed=17)
        self.assertEqual(result["simulator"], "SeQUeNCe")
        self.assertEqual(result["protocol"], "BB84 + Cascade")
        self.assertTrue(result["accepted"])
        self.assertLess(result["qber"], 0.11)
        self.assertGreater(result["matching_cascade_keys"], 0)
        self.assertTrue(all(len(key) == 32 for key in result["key_materials"]))

    def test_full_intercept_resend_is_rejected(self):
        result = run_sequence_bb84(LinkSettings(10, 1, 0.01), seed=17)
        self.assertFalse(result["accepted"])
        self.assertGreaterEqual(result["qber"], 0.11)
        self.assertEqual(result["matching_cascade_keys"], 0)

    def test_bad_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            run_sequence_bb84(LinkSettings(70, 0, 0), seed=17)


class KeyServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = QKDService()
        self.runner_patch = patch("qkd_demo.engine.run_sequence_bb84", side_effect=fake_experiment)
        self.runner_patch.start()

    def tearDown(self):
        self.runner_patch.stop()

    def test_message_round_trip_consumes_exactly_one_key(self):
        result = self.service.generate(1)
        self.assertEqual(result["generated"], 1)
        sent = self.service.send_message("Hello, Bob.")
        self.assertEqual(sent["bob_text"], "Hello, Bob.")
        self.assertEqual(sent["remaining_keys"], 0)
        self.assertGreater(len(sent["ciphertext"]), 0)

    def test_empty_pool_blocks_delivery(self):
        with self.assertRaisesRegex(RuntimeError, "no key is available"):
            self.service.send_message("No key")

    def test_failed_link_blocks_key_generation_and_messages(self):
        self.service.set_link(False)
        with self.assertRaisesRegex(RuntimeError, "link is down"):
            self.service.generate(1)
        with self.assertRaisesRegex(RuntimeError, "link is down"):
            self.service.send_message("Cannot deliver")

    def test_pool_capacity_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 24"):
            self.service.generate(25)

    def test_reset_clears_simulation_state(self):
        self.service.generate(1)
        self.service.set_link(False)
        state = self.service.reset()
        self.assertEqual(state["pool_count"], 0)
        self.assertTrue(state["link_up"])
        self.assertEqual(state["sent_count"], 0)


if __name__ == "__main__":
    unittest.main()

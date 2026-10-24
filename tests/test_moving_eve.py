import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from experiments.moving_eve import (
    _get_initial_state,
    classical_hop_handshake,
    classify_http_failure,
    message_exposed,
    run_scenario,
)


class MovingEveLogicTests(unittest.TestCase):
    def setUp(self):
        self.links = {
            frozenset(("alice", "r1")): {"id": "A-R1"},
            frozenset(("r1", "bob")): {"id": "R1-B"},
            frozenset(("alice", "r2")): {"id": "A-R2"},
            frozenset(("r2", "bob")): {"id": "R2-B"},
        }

    def test_qkd_exposure_requires_path_and_accepted_eve_link(self):
        path = ["alice", "r1", "bob"]
        self.assertTrue(message_exposed(path, {"A-R1"}, self.links))
        self.assertFalse(message_exposed(path, set(), self.links))
        self.assertFalse(message_exposed(path, {"R2-B"}, self.links))

    def test_unauthenticated_x25519_mitm_is_not_detected_and_splits_keys(self):
        result = classical_hop_handshake(eve_present=True, authenticated=False)
        self.assertTrue(result["established"])
        self.assertFalse(result["detected"])
        self.assertTrue(result["exposed"])
        self.assertNotEqual(result["alice_key"], result["bob_key"])

    def test_ed25519_authenticated_exchange_detects_substitution(self):
        attacked = classical_hop_handshake(eve_present=True, authenticated=True)
        self.assertFalse(attacked["established"])
        self.assertTrue(attacked["detected"])
        self.assertFalse(attacked["exposed"])
        clean = classical_hop_handshake(eve_present=False, authenticated=True)
        self.assertTrue(clean["established"])
        self.assertEqual(clean["alice_key"], clean["bob_key"])

    def test_request_timeout_is_not_reported_as_unreachable_coordinator(self):
        error = classify_http_failure(
            urllib.error.URLError(TimeoutError("operation timed out")),
            "http://127.0.0.1:8765",
        )
        self.assertIn("coordinator", str(error).lower())
        self.assertIn("reachable", str(error).lower())
        self.assertIn("timed out", str(error).lower())
        self.assertIn("several minutes", str(error).lower())
        self.assertNotIn("Cannot reach coordinator", str(error))

    def test_initial_state_retries_temporary_timeout(self):
        state = {"topology": True}
        with patch("experiments.moving_eve.get_json",
                   side_effect=[urllib.error.URLError(TimeoutError("busy")), state]) as request, \
             patch("experiments.moving_eve.time.sleep") as sleep:
            self.assertIs(_get_initial_state("http://127.0.0.1:8765"), state)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs["timeout"], 8)
        sleep.assert_called_once()

    def test_original_link_settings_are_restored_after_midrun_failure(self):
        original_links = [
            {"id": "A-R1", "a": "alice", "b": "r1", "distance_km": 8.0, "noise": .01, "eve": .1},
            {"id": "R1-B", "a": "r1", "b": "bob", "distance_km": 7.0, "noise": .02, "eve": .2},
            {"id": "A-R2", "a": "alice", "b": "r2", "distance_km": 10.0, "noise": .03, "eve": .3},
            {"id": "R2-B", "a": "r2", "b": "bob", "distance_km": 9.0, "noise": .04, "eve": .4},
        ]
        initial = {"topology": True, "links": original_links, "paths": []}
        posted = []

        def fake_post(_base_url, path, payload, **_kwargs):
            posted.append((path, payload))
            if path == "/api/reset":
                raise RuntimeError("simulated mid-run failure")
            return {}

        with patch("experiments.moving_eve._get_initial_state", return_value=initial), \
             patch("experiments.moving_eve.post_json", side_effect=fake_post):
            with self.assertRaisesRegex(RuntimeError, "simulated mid-run failure"):
                run_scenario("http://127.0.0.1:8765", Path("unused-moving-eve-test-output"), 1, .75,
                             scenarios=(("test", ()),))

        restored = {path.split("/")[-2]: payload for path, payload in posted
                    if path.startswith("/api/links/") and path.endswith("/settings")}
        self.assertEqual(set(restored), {link["id"] for link in original_links})
        for link in original_links:
            self.assertEqual(restored[link["id"]], {
                "distance_km": link["distance_km"],
                "channel_noise": link["noise"],
                "eve_fraction": link["eve"],
            })

if __name__ == "__main__":
    unittest.main()

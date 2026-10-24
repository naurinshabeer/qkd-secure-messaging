import unittest

from experiments.moving_eve import classical_hop_handshake, message_exposed


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


if __name__ == "__main__":
    unittest.main()

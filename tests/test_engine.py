import unittest
import time
from unittest.mock import patch

from qkd_demo.key_manager import KMEError, KeyManager
from qkd_demo.sequence_link import LinkSettings, run_sequence_bb84


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
        self.assertEqual(result["matching_cascade_keys"], len(result["key_materials"]))

    def test_bad_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            run_sequence_bb84(LinkSettings(70, 0, 0), seed=17)


class KeyManagerTests(unittest.TestCase):
    def test_reserve_commit_release_and_stale_reservation_expiry(self):
        manager = KeyManager("alice", "bob")
        manager.import_keys([("QK-ONE", b"a" * 32)])
        key_id, key = manager.reserve_enc("bob", "attempt-1")
        self.assertEqual((key_id, key), ("QK-ONE", b"a" * 32))
        with self.assertRaises(KMEError):
            manager.reserve_enc("bob", "attempt-2")
        manager.finish(key_id, "attempt-1", commit=False)
        self.assertEqual(manager.reserve_enc("bob", "attempt-2")[0], key_id)
        manager.finish(key_id, "attempt-2", commit=True)
        self.assertEqual(manager.total_count(), 0)

        manager.import_keys([("QK-TWO", b"b" * 32)])
        with patch("qkd_demo.key_manager.RESERVATION_TTL_SECONDS", 0.001):
            manager.reserve_enc("bob", "abandoned")
            time.sleep(0.01)
            self.assertEqual(manager.reserve_enc("bob", "after-expiry")[0], "QK-TWO")

    def test_key_sync_single_use_and_independent_pools(self):
        alice = KeyManager("alice", "bob")
        bob = KeyManager("bob", "alice")
        shared = [("QK-ONE", b"a" * 32)]
        alice.import_keys(shared)
        bob.import_keys(shared)
        key_id, alice_key = alice.enc_key("bob")
        bob_key = bob.dec_key("alice", key_id)
        self.assertEqual(alice_key, bob_key)
        self.assertEqual(alice.status("bob")["stored_key_count"], 0)
        self.assertEqual(bob.status("alice")["stored_key_count"], 0)
        with self.assertRaises(KMEError) as context:
            bob.dec_key("alice", key_id)
        self.assertEqual(context.exception.status, 409)

    def test_dec_key_unknown_and_wrong_peer(self):
        bob = KeyManager("bob", "alice")
        with self.assertRaises(KMEError) as unknown:
            bob.dec_key("alice", "missing")
        self.assertEqual(unknown.exception.status, 404)
        with self.assertRaises(KMEError) as peer:
            bob.status("mallory")
        self.assertEqual(peer.exception.status, 404)

    def test_import_is_atomic_and_validates_key_size(self):
        manager = KeyManager("alice", "bob")
        manager.import_keys([("QK-ONE", b"a" * 32)])
        with self.assertRaises(KMEError):
            manager.import_keys([("QK-TWO", b"b" * 32), ("QK-ONE", b"c" * 32)])
        self.assertEqual(manager.key_ids(), ["QK-ONE"])
        with self.assertRaises(KMEError):
            manager.import_keys([("QK-BAD", b"short")])


if __name__ == "__main__":
    unittest.main()

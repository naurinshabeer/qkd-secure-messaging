import base64
import json
import socket
import socketserver
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qkd_demo.coordinator import CoordinatorError, CoordinatorService
from qkd_demo.protocol import TYPE_MESSAGE, TYPE_NACK, Frame, recv_frame, send_frame
from support import NodeHarness, free_port


def fake_experiment(settings, *, seed=None):
    materials = [bytes([index + 1]) * 32 for index in range(5)]
    return {
        "simulator": "SeQUeNCe", "protocol": "BB84 + Cascade", "seed": seed or 17,
        "distance_km": settings.distance_km, "eve_fraction": settings.eve_fraction,
        "channel_noise": settings.channel_noise, "polarization_fidelity": 0.97,
        "signals_sent": 100_000, "signals_intercepted": 0, "qber_samples": 2,
        "qber": 0.02, "cascade_keys_generated": 5, "matching_cascade_keys": 5,
        "cascade_qber": 0.0, "cascade_throughput_bps": 1000,
        "cascade_privacy_throughput_bps": 900, "accepted": True,
        "key_materials": materials,
    }


def http_call(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method=method,
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.bob = NodeHarness("bob")
        self.alice = NodeHarness("alice", "127.0.0.1", self.bob.runtime.app_port)
        self.service = CoordinatorService(self.alice.base_url, self.bob.base_url)

    def tearDown(self):
        self.alice.close()
        self.bob.close()

    def generate(self, count=1):
        with patch("qkd_demo.coordinator.run_sequence_bb84", side_effect=fake_experiment):
            return self.service.generate(count)

    def test_key_sync_uses_same_id_and_material_via_separate_kmes(self):
        result = self.generate(2)
        state = result["state"]
        self.assertEqual(state["pool_count"], 2)
        self.assertEqual(state["bob_pool_count"], 2)
        alice_status, alice_key = http_call(
            self.alice.base_url + "/api/v1/keys/bob/enc_keys")
        key_id = alice_key["keys"][0]["key_ID"]
        bob_status, bob_key = http_call(
            self.bob.base_url + "/api/v1/keys/alice/dec_keys?" + urlencode({"key_ID": key_id}))
        self.assertEqual((alice_status, bob_status), (200, 200))
        self.assertEqual(alice_key["keys"][0], bob_key["keys"][0])
        self.assertEqual(key_id, result["state"]["key_ids"][0])

    def test_key_api_single_use_unknown_id_and_peer_codes(self):
        self.generate(2)
        status, allocated = http_call(self.alice.base_url + "/api/v1/keys/bob/enc_keys")
        self.assertEqual(status, 200)
        key_id = allocated["keys"][0]["key_ID"]
        post_status, second = http_call(self.alice.base_url + "/api/v1/keys/bob/enc_keys", "POST", {})
        self.assertEqual(post_status, 200)
        self.assertNotEqual(second["keys"][0]["key_ID"], key_id)
        status, repeated = http_call(self.alice.base_url + "/api/v1/keys/bob/enc_keys")
        self.assertEqual(status, 409)
        self.assertIn("No available key", repeated["error"])
        status, unknown_key = http_call(self.bob.base_url + "/api/v1/keys/alice/dec_keys?key_ID=missing")
        self.assertEqual(status, 404)
        status, unknown_peer = http_call(self.bob.base_url + "/api/v1/keys/mallory/status")
        self.assertEqual(status, 404)
        status, consumed = http_call(
            self.bob.base_url + "/api/v1/keys/alice/dec_keys?" + urlencode({"key_ID": key_id}))
        self.assertEqual(status, 200)
        status, second_use = http_call(
            self.bob.base_url + "/api/v1/keys/alice/dec_keys?" + urlencode({"key_ID": key_id}))
        self.assertEqual(status, 409)

    def test_end_to_end_alice_to_bob_over_tcp(self):
        self.generate(1)
        result = self.service.send_message("Hello over a real TCP socket")
        self.assertNotIn("bob_text", result)
        inbox_status, inbox = http_call(self.bob.base_url + "/api/node/inbox")
        self.assertEqual(inbox_status, 200)
        self.assertEqual(inbox["label"], "Bob's inbox")
        self.assertEqual(inbox["messages"][-1]["text"], "Hello over a real TCP socket")
        state = self.service.state()
        self.assertEqual(state["pool_count"], 0)
        self.assertEqual(state["bob_pool_count"], 0)
        self.assertEqual(state["sent_count"], 1)

    def test_tampered_ciphertext_is_nacked_and_bobs_key_is_released(self):
        self.generate(1)
        _, allocation = http_call(self.alice.base_url + "/api/v1/keys/bob/enc_keys")
        item = allocation["keys"][0]
        key = base64.b64decode(item["key"])
        key_id = item["key_ID"]
        nonce = b"n" * 12
        ciphertext = bytearray(AESGCM(key).encrypt(nonce, b"tamper me", key_id.encode("ascii")))
        ciphertext[-1] ^= 1
        with socket.create_connection(("127.0.0.1", self.bob.runtime.app_port), timeout=2) as sock:
            sock.settimeout(2)
            send_frame(sock, Frame(TYPE_MESSAGE, key_id, nonce, bytes(ciphertext), "TAMPER", 1))
            response = recv_frame(sock)
        self.assertEqual(response.message_type, TYPE_NACK)
        self.assertIn(b"Authentication failed", response.payload)
        status, returned = http_call(self.bob.base_url + "/api/v1/keys/alice/dec_keys?" + urlencode({"key_ID": key_id}))
        self.assertEqual(status, 200)

    def test_link_down_blocks_tcp_messaging_and_restore_allows_it(self):
        self.generate(1)
        self.service.set_link(False)
        with self.assertRaises(CoordinatorError) as failed:
            self.service.send_message("blocked")
        self.assertEqual(failed.exception.status, 503)
        alice_status, _ = http_call(self.alice.base_url + "/api/app/messages", "POST", {"text": "blocked"})
        self.assertEqual(alice_status, 503)
        with socket.create_connection(("127.0.0.1", self.bob.runtime.app_port), timeout=2) as sock:
            sock.settimeout(2)
            send_frame(sock, Frame(TYPE_MESSAGE, "QK-LINK-DOWN", bytes(12), b"ignored"))
            bob_reply = recv_frame(sock)
        self.assertEqual(bob_reply.message_type, TYPE_NACK)
        self.assertIn(b"link is down", bob_reply.payload)
        self.service.set_link(True)
        self.assertEqual(self.service.send_message("restored")["status"], "delivered")

    def test_empty_pool_and_unreachable_peer(self):
        with self.assertRaises(CoordinatorError) as empty:
            self.service.send_message("no key")
        self.assertEqual(empty.exception.status, 409)
        self.generate(1)
        self.alice.runtime.peer_port = free_port()
        with self.assertRaises(RuntimeError):
            self.alice.runtime.send_message("peer is offline")
        self.assertEqual(self.alice.runtime.kme.status("bob")["stored_key_count"], 1)

    def test_peer_read_timeout_is_handled(self):
        class SilentPeer(socketserver.BaseRequestHandler):
            def handle(self):
                time.sleep(3.5)

        silent_server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SilentPeer)
        silent_server.daemon_threads = True
        thread = threading.Thread(target=silent_server.serve_forever, daemon=True)
        thread.start()
        try:
            self.alice.runtime.peer_port = silent_server.server_address[1]
            self.alice.runtime.kme.import_keys([("QK-TIMEOUT", b"t" * 32)])
            with self.assertRaisesRegex(RuntimeError, "unreachable"):
                self.alice.runtime.send_message("wait for timeout")
            self.assertEqual(self.alice.runtime.kme.status("bob")["stored_key_count"], 1)
        finally:
            silent_server.shutdown()
            silent_server.server_close()
            thread.join(timeout=1)

    def test_bob_survives_oversized_and_truncated_tcp_frames(self):
        with socket.create_connection(("127.0.0.1", self.bob.runtime.app_port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall((1_000_000).to_bytes(4, "big"))
            oversized_reply = recv_frame(sock)
            self.assertEqual(oversized_reply.message_type, TYPE_NACK)
        with socket.create_connection(("127.0.0.1", self.bob.runtime.app_port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(b"\x00\x00\x00\x40partial")
            sock.shutdown(socket.SHUT_WR)
            truncated_reply = recv_frame(sock)
            self.assertEqual(truncated_reply.message_type, TYPE_NACK)
        self.generate(1)
        self.assertEqual(self.service.send_message("listener remains alive")["status"],
                         "delivered")


if __name__ == "__main__":
    unittest.main()

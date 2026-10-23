import base64
import json
import socket
import socketserver
import os
import subprocess
import sys
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qkd_demo.coordinator import CoordinatorError, CoordinatorService
from qkd_demo.protocol import TYPE_MESSAGE, TYPE_NACK, TYPE_OUTCOME_QUERY, Frame, recv_frame, send_frame
from qkd_demo.node import message_aad
from support import NodeHarness, TEST_TOKEN, free_port


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


def http_call(url, method="GET", payload=None, token=TEST_TOKEN):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, data=data, method=method,
                      headers=headers)
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

    def test_provisioning_requires_correct_token(self):
        url = self.alice.base_url + "/api/node/import"
        row = {"key_ID": "QK-AUTH", "key": base64.b64encode(b"x" * 32).decode("ascii")}
        self.assertEqual(http_call(url, "POST", {"keys": [row]}, token=None)[0], 403)
        self.assertEqual(http_call(url, "POST", {"keys": [row]}, token="wrong")[0], 403)
        self.assertEqual(http_call(url, "POST", {"keys": [row]}, token=TEST_TOKEN)[0], 200)

    def test_node_and_coordinator_refuse_start_without_token(self):
        environment = os.environ.copy()
        environment.pop("QKD_PROVISION_TOKEN", None)
        for command in (
            [sys.executable, "-m", "qkd_demo.node", "--role", "bob"],
            [sys.executable, "-m", "qkd_demo.coordinator"],
        ):
            result = subprocess.run(command, cwd=os.getcwd(), env=environment,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertIn("QKD_PROVISION_TOKEN", result.stderr)

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
        self.assertEqual(response.payload[:1], b"\x02")
        status, returned = http_call(self.bob.base_url + "/api/v1/keys/alice/dec_keys?" + urlencode({"key_ID": key_id}))
        self.assertEqual(status, 200)

    def test_altered_message_metadata_fails_gcm_authentication(self):
        self.generate(1)
        _, allocated = http_call(self.alice.base_url + "/api/v1/keys/bob/enc_keys")
        item = allocated["keys"][0]
        key, key_id = base64.b64decode(item["key"]), item["key_ID"]
        session, message_id, nonce, seq = "test-session", "original-id", b"z" * 12, 7
        ciphertext = AESGCM(key).encrypt(nonce, b"metadata must authenticate",
                                         message_aad(key_id, message_id, seq, session))
        forged = Frame(TYPE_MESSAGE, key_id, nonce, ciphertext, "altered-id", seq, session)
        with socket.create_connection(("127.0.0.1", self.bob.runtime.app_port), timeout=2) as sock:
            sock.settimeout(2)
            send_frame(sock, forged)
            reply = recv_frame(sock)
        self.assertEqual(reply.message_type, TYPE_NACK)
        self.assertEqual(reply.payload[:1], b"\x02")

    def test_same_frame_replay_is_rejected(self):
        self.generate(1)
        _, allocated = http_call(self.alice.base_url + "/api/v1/keys/bob/enc_keys")
        item = allocated["keys"][0]
        key, key_id = base64.b64decode(item["key"]), allocated["keys"][0]["key_ID"]
        session, message_id, nonce, seq = "replay-session", "replay-id", b"r" * 12, 1
        ciphertext = AESGCM(key).encrypt(nonce, b"once", message_aad(key_id, message_id, seq, session))
        frame = Frame(TYPE_MESSAGE, key_id, nonce, ciphertext, message_id, seq, session)
        replies = []
        for _ in range(2):
            with socket.create_connection(("127.0.0.1", self.bob.runtime.app_port), timeout=2) as sock:
                sock.settimeout(2); send_frame(sock, frame); replies.append(recv_frame(sock))
        self.assertEqual(replies[0].message_type, 2)
        self.assertEqual(replies[1].message_type, TYPE_NACK)
        self.assertIn(b"Replay", replies[1].payload)

    def test_alice_restart_uses_new_session_and_can_send(self):
        self.generate(2)
        old_runtime = self.alice.runtime
        manager = old_runtime.kme
        bob_app = self.bob.runtime.app_port
        self.alice.close()
        self.alice = NodeHarness("alice", "127.0.0.1", bob_app)
        self.alice.runtime.kme = manager
        self.service.node_urls["alice"] = self.alice.base_url
        self.assertEqual(self.service.send_message("after Alice restart")["status"], "delivered")

    def test_lost_ack_queries_bob_and_keeps_pools_consistent(self):
        self.generate(2)
        bob_port = self.bob.runtime.app_port
        class DropFirstAck(socketserver.BaseRequestHandler):
            dropped = False
            def handle(handler_self):
                try:
                    request = recv_frame(handler_self.request)
                    with socket.create_connection(("127.0.0.1", bob_port), timeout=2) as remote:
                        remote.settimeout(2); send_frame(remote, request); reply = recv_frame(remote)
                    if request.message_type == TYPE_MESSAGE and not DropFirstAck.dropped:
                        DropFirstAck.dropped = True
                        return
                    send_frame(handler_self.request, reply)
                except (OSError, TimeoutError, ValueError):
                    return
        proxy = socketserver.ThreadingTCPServer(("127.0.0.1", 0), DropFirstAck)
        proxy.daemon_threads = True
        thread = threading.Thread(target=proxy.serve_forever, daemon=True); thread.start()
        try:
            self.alice.runtime.peer_port = proxy.server_address[1]
            first = self.alice.runtime.send_message("ACK dropped after commit")
            self.assertEqual(first["status"], "delivered")
            state = self.service.state()
            self.assertEqual(state["pool_count"], state["bob_pool_count"])
            second = self.alice.runtime.send_message("next message still works")
            self.assertEqual(second["status"], "delivered")
            state = self.service.state()
            self.assertEqual(state["pool_count"], state["bob_pool_count"])
        finally:
            proxy.shutdown(); proxy.server_close(); thread.join(timeout=1)

    def test_ack_hmac_mismatch_is_resolved_by_outcome_query(self):
        self.generate(1)
        bob_port = self.bob.runtime.app_port
        class CorruptAck(socketserver.BaseRequestHandler):
            corrupted = False
            def handle(handler_self):
                request = recv_frame(handler_self.request)
                with socket.create_connection(("127.0.0.1", bob_port), timeout=2) as remote:
                    remote.settimeout(2); send_frame(remote, request); reply = recv_frame(remote)
                if request.message_type == TYPE_MESSAGE and not CorruptAck.corrupted:
                    CorruptAck.corrupted = True
                    reply = Frame(reply.message_type, reply.key_id, reply.nonce,
                                  bytes([reply.payload[0]]) + bytes([reply.payload[1] ^ 1]) + reply.payload[2:],
                                  reply.message_id, reply.sequence, reply.session_id)
                send_frame(handler_self.request, reply)
        proxy = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CorruptAck)
        proxy.daemon_threads = True
        thread = threading.Thread(target=proxy.serve_forever, daemon=True); thread.start()
        try:
            self.alice.runtime.peer_port = proxy.server_address[1]
            self.assertEqual(self.alice.runtime.send_message("bad ack MAC")["status"], "delivered")
            self.assertEqual(self.service.state()["pool_count"], self.service.state()["bob_pool_count"])
        finally:
            proxy.shutdown(); proxy.server_close(); thread.join(timeout=1)

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
            with self.assertRaisesRegex(RuntimeError, "unknown"):
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

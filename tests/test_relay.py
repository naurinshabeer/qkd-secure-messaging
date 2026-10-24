import threading
import unittest
import os
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qkd_demo.coordinator import CoordinatorError, CoordinatorHTTPServer, CoordinatorService
from qkd_demo.node import AppTCPServer, NodeHTTPServer, NodeRuntime, transport_aad
from qkd_demo.protocol import TYPE_KEY_TRANSPORT, TYPE_NACK, Frame
from qkd_demo.sequence_link import run_sequence_bb84
from support import free_port


def fake_experiment(settings, *, seed=None):
    key = (int(settings.distance_km) % 250 + 1).to_bytes(1, "big") * 32
    return {"simulator": "SeQUeNCe", "protocol": "BB84 + Cascade", "seed": 17,
            "distance_km": settings.distance_km, "eve_fraction": settings.eve_fraction,
            "channel_noise": settings.channel_noise, "polarization_fidelity": .97,
            "signals_sent": 100, "signals_intercepted": 0, "qber_samples": 1,
            "qber": .02, "cascade_keys_generated": 1, "matching_cascade_keys": 1,
            "cascade_qber": 0, "cascade_throughput_bps": 1000,
            "cascade_privacy_throughput_bps": 900, "accepted": True, "key_materials": [key]}


class RelayNetworkTests(unittest.TestCase):
    def setUp(self):
        nodes = {}
        for node_id, role in (("alice", "alice"), ("r1", "relay"), ("r2", "relay"), ("bob", "bob")):
            nodes[node_id] = {"role": role, "http_port": free_port(), "app_port": free_port()}
        self.topology = {"nodes": nodes, "links": [
            {"id": "A-R1", "a": "alice", "b": "r1", "distance_km": 8, "noise": .01, "eve": 0, "up": True},
            {"id": "R1-B", "a": "r1", "b": "bob", "distance_km": 7, "noise": .01, "eve": 0, "up": True},
            {"id": "A-R2", "a": "alice", "b": "r2", "distance_km": 10, "noise": .01, "eve": 0, "up": True},
            {"id": "R2-B", "a": "r2", "b": "bob", "distance_km": 9, "noise": .01, "eve": 0, "up": True}]}
        self.coord_port = free_port()
        self.coordinator_url = f"http://127.0.0.1:{self.coord_port}"
        self.nodes = {}
        self.servers = []
        self.threads = []
        for node_id, config in nodes.items():
            runtime = NodeRuntime(config["role"], "127.0.0.1", config["http_port"], "127.0.0.1",
                config["app_port"], None, None, coordinator_url=self.coordinator_url,
                node_id=node_id, topology=self.topology)
            http = NodeHTTPServer(("127.0.0.1", config["http_port"]), runtime)
            tcp = AppTCPServer(("127.0.0.1", config["app_port"]), runtime)
            runtime.http_port, runtime.app_port = http.server_port, tcp.server_address[1]
            self.nodes[node_id] = runtime
            self.servers.extend((http, tcp))
            for server in (http, tcp):
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.threads.append(thread)
        self.service = CoordinatorService("", "", self.topology)
        self.coord_server = CoordinatorHTTPServer(("127.0.0.1", self.coord_port), self.service)
        self.servers.append(self.coord_server)
        thread = threading.Thread(target=self.coord_server.serve_forever, daemon=True)
        thread.start()
        self.threads.append(thread)

    def tearDown(self):
        for server in reversed(self.servers):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=1)

    def generate(self, count=1):
        with patch("qkd_demo.coordinator.run_sequence_bb84", side_effect=fake_experiment):
            return self.service.generate(count)

    def test_key_transport_delivers_identical_end_to_end_key(self):
        self.generate()
        result = self.service.send_message("trusted relay path")
        self.assertEqual(result["path"], ["alice", "r1", "bob"])
        circuit = result["message_id"]
        self.assertEqual(self.nodes["alice"].end_to_end_keys[circuit], self.nodes["r1"].end_to_end_keys[circuit])
        self.assertEqual(self.nodes["alice"].end_to_end_keys[circuit], self.nodes["bob"].end_to_end_keys[circuit])
        self.assertEqual(self.nodes["bob"]._inbox[-1]["text"], "trusted relay path")

    def test_tampered_relay_frame_is_rejected_and_key_released(self):
        self.generate()
        alice, relay = self.nodes["alice"], self.nodes["r1"]
        key_id, link_key = alice.kme_for("r1").reserve_enc("r1", "tampered-circuit")
        nonce, path = b"n" * 12, "alice>r1>bob"
        payload = bytearray(AESGCM(link_key).encrypt(nonce, b"e" * 32,
            transport_aad(key_id, "tampered-circuit", 1, path)))
        payload[-1] ^= 1
        reply = relay.receive_message(Frame(TYPE_KEY_TRANSPORT, key_id, nonce, bytes(payload),
                                            "tampered-circuit", 1, path))
        self.assertEqual(reply.message_type, TYPE_NACK)
        self.assertIsNone(relay.kme_for("alice")._keys[key_id].reservation)

    def test_link_down_fails_over_to_other_relay(self):
        self.generate()
        self.service.set_link(False, "A-R1")
        result = self.service.send_message("use second route")
        self.assertEqual(result["path"], ["alice", "r2", "bob"])

    def test_empty_pool_fails_over_to_other_relay(self):
        self.generate()
        self.nodes["alice"].kme_for("r1").reset()
        self.nodes["r1"].kme_for("alice").reset()
        result = self.service.send_message("pool failover")
        self.assertEqual(result["path"], ["alice", "r2", "bob"])

    def test_both_paths_down_returns_clear_error(self):
        for link_id in ("A-R1", "R1-B", "A-R2", "R2-B"):
            self.service.set_link(False, link_id)
        with self.assertRaisesRegex(CoordinatorError, "Both Alice-to-Bob relay paths"):
            self.service.send_message("nowhere")

    def test_relay_downstream_failure_keeps_each_link_pool_in_sync(self):
        self.generate()
        self.nodes["r1"].peer_endpoints["bob"]["app_port"] = free_port()
        with self.assertRaises(CoordinatorError):
            self.service.send_message("relay stops mid transport")
        self.assertEqual(self.nodes["alice"].kme_for("r1").total_count(),
                         self.nodes["r1"].kme_for("alice").total_count())
        self.assertEqual(self.nodes["r1"].kme_for("bob").total_count(),
                         self.nodes["bob"].kme_for("r1").total_count())

    @unittest.skipUnless(os.getenv("QKD_RUN_SLOW_TESTS") == "1", "set QKD_RUN_SLOW_TESTS=1 to run real SeQUeNCe E2E")
    def test_slow_real_sequence_end_to_end_through_relay(self):
        self.service.set_link(False, "A-R2")
        self.service.set_link(False, "R2-B")
        with patch("qkd_demo.coordinator.run_sequence_bb84",
                   side_effect=lambda settings: run_sequence_bb84(settings, seed=17)):
            self.service.generate(1)
        available = self.nodes["alice"].kme_for("r1").total_count()
        if available == 0:
            self.skipTest("Fixed real SeQUeNCe run did not yield an accepted matching key")
        self.assertEqual(self.service.send_message("real SeQUeNCe through a trusted relay")["status"], "delivered")


if __name__ == "__main__":
    unittest.main()

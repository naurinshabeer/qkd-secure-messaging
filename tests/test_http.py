import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from qkd_demo.coordinator import CoordinatorHTTPServer, CoordinatorService
from support import NodeHarness, TEST_TOKEN


def fake_experiment(settings, *, seed=None):
    return {"simulator": "SeQUeNCe", "protocol": "BB84 + Cascade", "seed": 17,
            "distance_km": settings.distance_km, "eve_fraction": settings.eve_fraction,
            "channel_noise": settings.channel_noise, "polarization_fidelity": 0.97,
            "signals_sent": 100_000, "signals_intercepted": 0, "qber_samples": 2,
            "qber": 0.02, "cascade_keys_generated": 1, "matching_cascade_keys": 1,
            "cascade_qber": 0.0, "cascade_throughput_bps": 1000,
            "cascade_privacy_throughput_bps": 900, "accepted": True,
            "key_materials": [b"k" * 32]}


class CoordinatorHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bob = NodeHarness("bob")
        cls.alice = NodeHarness("alice", "127.0.0.1", cls.bob.runtime.app_port)
        service = CoordinatorService(cls.alice.base_url, cls.bob.base_url)
        cls.server = CoordinatorHTTPServer(("127.0.0.1", 0), service)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.service = service
        cls.alice.runtime.coordinator_url = cls.base
        cls.bob.runtime.coordinator_url = cls.base

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.alice.close()
        cls.bob.close()

    def post(self, route, payload):
        request = Request(self.base + route, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_frontend_and_state_are_served_without_keys(self):
        with urlopen(self.base + "/", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"Send a message using a shared secret", response.read())
        for asset in ("/console.css", "/app.js"):
            with urlopen(self.base + asset, timeout=3) as response:
                self.assertEqual(response.status, 200)
        with urlopen(self.base + "/api/state", timeout=3) as response:
            state = json.loads(response.read())
        self.assertTrue(state["alice_online"])
        self.assertTrue(state["bob_online"])
        self.assertNotIn("key", state)

    def test_http_generate_and_message_use_bob_process(self):
        with patch("qkd_demo.coordinator.run_sequence_bb84", side_effect=fake_experiment):
            status, generated = self.post("/api/keys/generate", {"count": 1})
        self.assertEqual(status, 200)
        self.assertEqual(generated["state"]["pool_count"], 1)
        self.assertEqual(generated["state"]["bob_pool_count"], 1)
        status, delivered = self.post("/api/messages", {"text": "Through coordinator"})
        self.assertEqual(status, 200)
        self.assertNotIn("bob_text", delivered)
        with urlopen(self.bob.base_url + "/api/node/inbox", timeout=3) as response:
            inbox = json.loads(response.read())
        self.assertEqual(inbox["messages"][-1]["text"], "Through coordinator")
        state = self.service.state()
        self.assertEqual(state["pool_count"], 0)
        self.assertEqual(state["bob_pool_count"], 0)

    def test_coordinator_owned_link_state_blocks_node_provisioning(self):
        with urlopen(self.base + "/api/link-state", timeout=2) as response:
            self.assertTrue(json.loads(response.read())["up"])
        self.service.set_link(False)
        with urlopen(self.base + "/api/link-state", timeout=2) as response:
            self.assertFalse(json.loads(response.read())["up"])
        request = Request(self.alice.base_url + "/api/node/import",
                          data=json.dumps({"keys": []}).encode(), method="POST",
                          headers={"Content-Type": "application/json",
                                   "Authorization": "Bearer " + TEST_TOKEN})
        with self.assertRaises(HTTPError) as blocked:
            urlopen(request, timeout=2)
        self.assertEqual(blocked.exception.code, 503)
        self.service.set_link(True)


if __name__ == "__main__":
    unittest.main()

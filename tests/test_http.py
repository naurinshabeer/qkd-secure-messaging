import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from qkd_demo.server import DemoServer, Handler


def fake_experiment(settings, *, seed=None):
    return {"simulator": "SeQUeNCe", "protocol": "BB84 + Cascade", "seed": 17,
            "distance_km": settings.distance_km, "eve_fraction": settings.eve_fraction,
            "channel_noise": settings.channel_noise, "polarization_fidelity": 0.97,
            "signals_sent": 100_000, "signals_intercepted": 0, "qber_samples": 2,
            "qber": 0.02, "cascade_keys_generated": 1, "matching_cascade_keys": 1,
            "cascade_qber": 0.0, "cascade_throughput_bps": 1000,
            "cascade_privacy_throughput_bps": 900, "accepted": True,
            "key_materials": [b"k" * 32]}


class HttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = DemoServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.server.service.reset()

    def post(self, route, payload):
        request = Request(self.base + route,
                          data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"},
                          method="POST")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_frontend_is_served(self):
        with urlopen(self.base + "/", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"BB84", response.read())
        for path in ("/console.css", "/app.js"):
            with urlopen(self.base + path, timeout=3) as response:
                self.assertEqual(response.status, 200)

    def test_api_generates_a_key_then_delivers_message(self):
        with patch("qkd_demo.engine.run_sequence_bb84", side_effect=fake_experiment):
            status, generated = self.post("/api/keys/generate", {"count": 1})
        self.assertEqual(status, 200)
        self.assertEqual(generated["generated"], 1)
        status, delivered = self.post("/api/messages", {"text": "HTTP round trip"})
        self.assertEqual(status, 200)
        self.assertEqual(delivered["bob_text"], "HTTP round trip")
        self.assertEqual(delivered["remaining_keys"], 0)

    def test_api_returns_conflict_when_pool_is_empty(self):
        status, response = self.post("/api/messages", {"text": "No key"})
        self.assertEqual(status, 409)
        self.assertIn("no key is available", response["error"])


if __name__ == "__main__":
    unittest.main()

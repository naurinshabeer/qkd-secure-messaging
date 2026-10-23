"""In-memory key management and the encrypted Alice-to-Bob demonstration."""

from __future__ import annotations

import base64
import secrets
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qkd_demo.sequence_link import LinkSettings, run_sequence_bb84

QBER_LIMIT = 0.11
POOL_LIMIT = 24
MESSAGE_KEY_BYTES = 32


class QKDService:
    """SeQUeNCe-backed link experiments, a finite pool, and local messaging."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.settings = LinkSettings()
        self.link_up = True
        self.pool: deque[dict[str, Any]] = deque()
        self.events: deque[dict[str, Any]] = deque(maxlen=30)
        self.last_run: dict[str, Any] | None = None
        self.sent_count = 0
        self.blocked_count = 0
        self.started_at = time.time()
        self._event("Prototype ready · SeQUeNCe key source selected", "info")

    def _event(self, message: str, level: str) -> None:
        self.events.appendleft({"id": uuid.uuid4().hex[:8], "time": time.time(),
                                "message": message, "level": level})

    def set_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.settings
        settings = LinkSettings(
            distance_km=float(payload.get("distance_km", current.distance_km)),
            eve_fraction=float(payload.get("eve_fraction", current.eve_fraction)),
            channel_noise=float(payload.get("channel_noise", current.channel_noise)),
        )
        with self._lock:
            self.settings = settings
            self._event("Next SeQUeNCe experiment settings updated", "info")
            return self.state()

    def generate(self, count: int = POOL_LIMIT) -> dict[str, Any]:
        """Run one physical-link simulation and import available Cascade keys."""
        if not isinstance(count, int) or not 1 <= count <= POOL_LIMIT:
            raise ValueError(f"Import limit must be between 1 and {POOL_LIMIT} keys.")
        with self._lock:
            if not self.link_up:
                raise RuntimeError("The link is down. Restore it before generating keys.")
            room = POOL_LIMIT - len(self.pool)
            if room == 0:
                raise ValueError("The key pool is full. Send a message before generating more.")

            result = run_sequence_bb84(self.settings)
            key_materials = result.pop("key_materials")
            result["accepted"] = bool(result["accepted"] and result["qber"] < QBER_LIMIT)
            import_limit = min(count, room)
            imported = key_materials[:import_limit] if result["accepted"] else []
            for key in imported:
                self.pool.append({"id": "QK-" + uuid.uuid4().hex[:10].upper(),
                                  "material": key, "created_at": time.time()})

            result["pool_added"] = len(imported)
            result["pool_available"] = len(self.pool)
            result["cascade_keys_not_imported"] = max(0, len(key_materials) - len(imported))
            self.last_run = result
            level = "success" if imported else "warning"
            if imported:
                message = (f"SeQUeNCe BB84 + Cascade finished · {len(imported)} key(s) added · "
                           f"QBER {result['qber']:.2%}")
            elif result["qber"] >= QBER_LIMIT:
                message = f"SeQUeNCe batch rejected · QBER {result['qber']:.2%} exceeded the teaching cutoff"
            else:
                message = "SeQUeNCe batch completed without matching Cascade keys"
            self._event(message, level)
            return {"generated": len(imported),
                    "available_from_experiment": len(key_materials),
                    "state": self.state()}

    def set_link(self, up: bool) -> dict[str, Any]:
        with self._lock:
            self.link_up = bool(up)
            self._event("Link restored" if up else "Link failed", "success" if up else "warning")
            return self.state()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self.settings = LinkSettings()
            self.link_up = True
            self.pool.clear()
            self.events.clear()
            self.last_run = None
            self.sent_count = 0
            self.blocked_count = 0
            self.started_at = time.time()
            self._event("Demonstration reset", "info")
            return self.state()

    def send_message(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Enter a message before sending.")
        if len(text) > 2000:
            raise ValueError("Messages are limited to 2,000 characters.")
        with self._lock:
            if not self.link_up:
                self.blocked_count += 1
                self._event("Message blocked: link is down", "warning")
                raise RuntimeError("Message not sent: the simulated link is down.")
            if not self.pool:
                self.blocked_count += 1
                self._event("Message blocked: key pool is empty", "warning")
                raise RuntimeError("Message not sent: no key is available. Run SeQUeNCe first.")

            key_record = self.pool.popleft()
            key = key_record["material"]
            nonce = secrets.token_bytes(12)
            aad = key_record["id"].encode("ascii")
            ciphertext = AESGCM(key).encrypt(nonce, text.encode("utf-8"), aad)
            recovered = AESGCM(key).decrypt(nonce, ciphertext, aad).decode("utf-8")
            self.sent_count += 1
            self._event(f"Message delivered to Bob using {key_record['id']}", "success")
            return {
                "message_id": "MSG-" + uuid.uuid4().hex[:8].upper(),
                "key_id": key_record["id"],
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                "ciphertext_bytes": len(ciphertext),
                "bob_text": recovered,
                "remaining_keys": len(self.pool),
            }

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "link_up": self.link_up,
                "settings": asdict(self.settings),
                "pool_count": len(self.pool),
                "pool_limit": POOL_LIMIT,
                "key_ids": [item["id"] for item in self.pool],
                "sent_count": self.sent_count,
                "blocked_count": self.blocked_count,
                "last_run": self.last_run,
                "events": list(self.events),
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
                "key_bytes": MESSAGE_KEY_BYTES,
                "qber_limit": QBER_LIMIT,
                "simulator": "SeQUeNCe",
            }


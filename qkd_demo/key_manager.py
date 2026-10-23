"""Small in-memory KME with single-use key delivery semantics."""

from __future__ import annotations

import threading
from dataclasses import dataclass

POOL_LIMIT = 24


class KMEError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class KeyRecord:
    key_id: str
    material: bytes


class KeyManager:
    """Owns one endpoint's copy of keys; raw keys leave only via key APIs."""

    def __init__(self, own_id: str, peer_id: str, limit: int = POOL_LIMIT):
        self.own_id = own_id
        self.peer_id = peer_id
        self.limit = limit
        self._keys: dict[str, KeyRecord] = {}
        self._used_ids: set[str] = set()
        self._lock = threading.RLock()

    def _check_peer(self, peer_id: str) -> None:
        if peer_id != self.peer_id:
            raise KMEError(404, "Unknown peer_id.")

    def import_keys(self, keys: list[tuple[str, bytes]]) -> None:
        """Install a simulator-created batch atomically; duplicate IDs fail."""
        with self._lock:
            if len(self._keys) + len(keys) > self.limit:
                raise KMEError(409, "Key pool capacity would be exceeded.")
            ids = [key_id for key_id, _ in keys]
            if len(ids) != len(set(ids)) or any(key_id in self._keys or key_id in self._used_ids
                                                for key_id in ids):
                raise KMEError(409, "Key ID already exists.")
            if any(len(material) != 32 for _, material in keys):
                raise KMEError(400, "Each key must contain exactly 32 bytes.")
            if any(not key_id or len(key_id) > 64 for key_id in ids):
                raise KMEError(400, "Invalid key_ID.")
            for key_id, material in keys:
                self._keys[key_id] = KeyRecord(key_id, material)

    def remove_keys(self, key_ids: list[str]) -> None:
        with self._lock:
            for key_id in key_ids:
                self._keys.pop(key_id, None)

    def status(self, peer_id: str) -> dict[str, object]:
        self._check_peer(peer_id)
        with self._lock:
            return {
                "source_KME_ID": self.own_id,
                "target_KME_ID": peer_id,
                "stored_key_count": len(self._keys),
                "max_key_count": self.limit,
            }

    def key_ids(self) -> list[str]:
        with self._lock:
            return list(self._keys)

    def total_count(self) -> int:
        with self._lock:
            return len(self._keys)

    def enc_key(self, peer_id: str) -> tuple[str, bytes]:
        self._check_peer(peer_id)
        with self._lock:
            if self._keys:
                key_id = next(iter(self._keys))
                record = self._keys.pop(key_id)
                self._used_ids.add(key_id)
                return key_id, record.material
        raise KMEError(409, "No available key.")

    def dec_key(self, peer_id: str, key_id: str) -> bytes:
        self._check_peer(peer_id)
        with self._lock:
            record = self._keys.get(key_id)
            if record is None:
                if key_id in self._used_ids:
                    raise KMEError(409, "Key has already been consumed.")
                raise KMEError(404, "Unknown key_ID.")
            self._used_ids.add(key_id)
            del self._keys[key_id]
            return record.material

    def reset(self) -> None:
        with self._lock:
            self._keys.clear()
            self._used_ids.clear()

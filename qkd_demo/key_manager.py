"""Small in-memory KME with single-use key delivery semantics."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

POOL_LIMIT = 24
USED_ID_LIMIT = 4096
RESERVATION_TTL_SECONDS = 30.0


class KMEError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class KeyRecord:
    key_id: str
    material: bytes
    reservation: str | None = None
    reserved_at: float = 0.0


class KeyManager:
    """Owns one endpoint's copy of keys; raw keys leave only via key APIs."""

    def __init__(self, own_id: str, peer_id: str, limit: int = POOL_LIMIT):
        self.own_id = own_id
        self.peer_id = peer_id
        self.limit = limit
        self._keys: dict[str, KeyRecord] = {}
        # Bounded replay/duplicate history. See README for eviction trade-off.
        self._used_ids: OrderedDict[str, None] = OrderedDict()
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

    def _remember_used(self, key_id: str) -> None:
        self._used_ids[key_id] = None
        self._used_ids.move_to_end(key_id)
        while len(self._used_ids) > USED_ID_LIMIT:
            self._used_ids.popitem(last=False)

    def _expire_reservations(self) -> None:
        cutoff = time.monotonic() - RESERVATION_TTL_SECONDS
        for record in self._keys.values():
            if record.reservation is not None and record.reserved_at < cutoff:
                record.reservation = None
                record.reserved_at = 0.0

    def status(self, peer_id: str) -> dict[str, object]:
        self._check_peer(peer_id)
        with self._lock:
            self._expire_reservations()
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
            self._expire_reservations()
            for key_id, record in self._keys.items():
                if record.reservation is not None:
                    continue
                self._remember_used(key_id)
                del self._keys[key_id]
                return key_id, record.material
        raise KMEError(409, "No available key.")

    def dec_key(self, peer_id: str, key_id: str) -> bytes:
        self._check_peer(peer_id)
        with self._lock:
            self._expire_reservations()
            record = self._keys.get(key_id)
            if record is None:
                if key_id in self._used_ids:
                    raise KMEError(409, "Key has already been consumed.")
                raise KMEError(404, "Unknown key_ID.")
            if record.reservation is not None:
                raise KMEError(409, "Key is reserved.")
            self._remember_used(key_id)
            del self._keys[key_id]
            return record.material

    def reserve_enc(self, peer_id: str, reservation: str) -> tuple[str, bytes]:
        self._check_peer(peer_id)
        with self._lock:
            self._expire_reservations()
            for key_id, record in self._keys.items():
                if record.reservation is None:
                    record.reservation = reservation
                    record.reserved_at = time.monotonic()
                    return key_id, record.material
        raise KMEError(409, "No available key.")

    def reserve_dec(self, peer_id: str, key_id: str, reservation: str) -> bytes:
        self._check_peer(peer_id)
        with self._lock:
            self._expire_reservations()
            record = self._keys.get(key_id)
            if record is None:
                raise KMEError(409 if key_id in self._used_ids else 404,
                               "Key has already been consumed." if key_id in self._used_ids else "Unknown key_ID.")
            if record.reservation not in (None, reservation):
                raise KMEError(409, "Key is reserved.")
            record.reservation = reservation
            record.reserved_at = time.monotonic()
            return record.material

    def finish(self, key_id: str, reservation: str, *, commit: bool) -> None:
        with self._lock:
            self._expire_reservations()
            record = self._keys.get(key_id)
            if record is None:
                if key_id in self._used_ids and commit:
                    return
                raise KMEError(404, "Unknown key_ID.")
            if record.reservation != reservation:
                raise KMEError(409, "Reservation does not match.")
            if commit:
                del self._keys[key_id]
                self._remember_used(key_id)
            else:
                record.reservation = None
                record.reserved_at = 0.0

    def discard(self, key_id: str) -> None:
        with self._lock:
            self._keys.pop(key_id, None)
            self._remember_used(key_id)

    def reset(self) -> None:
        with self._lock:
            self._keys.clear()
            self._used_ids.clear()

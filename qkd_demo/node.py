"""Run one independent Alice or Bob node with its own KME and app listener."""

from __future__ import annotations

import argparse
import base64
import json
import hashlib
import hmac
import os
import secrets
import socket
import socketserver
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qkd_demo.key_manager import KMEError, KeyManager
from qkd_demo.protocol import (
    TYPE_ACK, TYPE_MESSAGE, TYPE_NACK, TYPE_OUTCOME, TYPE_OUTCOME_QUERY, TYPE_KEY_TRANSPORT, Frame, ProtocolError, recv_frame, send_frame,
)
from qkd_demo.topology import TopologyError, load_topology, validate_topology

HTTP_TIMEOUT = 3.0
SOCKET_TIMEOUT = 3.0


def message_aad(key_id: str, message_id: str, sequence: int, session_id: str) -> bytes:
    return (key_id.encode("ascii") + b"\x00" + message_id.encode("ascii") + b"\x00" +
            sequence.to_bytes(8, "big") + b"\x00" + session_id.encode("ascii"))


def transport_aad(key_id: str, message_id: str, sequence: int, path: str) -> bytes:
    return b"QKD-RELAY\x00" + message_aad(key_id, message_id, sequence, path)


def json_request(url: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("QKD_PROVISION_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=data, method=method,
                                    headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
            message = body.get("error", "Remote node rejected request.")
        except (ValueError, OSError):
            message = "Remote node rejected request."
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
    except (OSError, TimeoutError) as exc:
        raise RuntimeError("Peer node is unreachable or timed out.") from exc


class NodeRuntime:
    def __init__(self, role: str, bind_host: str, http_port: int, app_host: str,
                 app_port: int, peer_host: str | None, peer_port: int | None,
                 *, insecure_dev: bool = False, coordinator_url: str | None = None,
                 node_id: str | None = None, topology: dict[str, Any] | None = None):
        if role not in ("alice", "bob", "relay"):
            raise ValueError("role must be alice, bob, or relay")
        if topology is not None:
            validate_topology(topology)
        self.role = role
        self.topology = topology or {}
        self.own_id = node_id or role
        self.peer_id = "bob" if role == "alice" else "alice"
        self.bind_host = bind_host
        self.http_port = http_port
        self.app_host = app_host
        self.app_port = app_port
        self.peer_host = peer_host
        self.peer_port = peer_port
        self.provision_token = os.getenv("QKD_PROVISION_TOKEN")
        self.insecure_dev = insecure_dev
        self.coordinator_url = coordinator_url.rstrip("/") if coordinator_url else None
        self.peer_endpoints: dict[str, dict[str, Any]] = {}
        self.node_endpoints: dict[str, dict[str, Any]] = {}
        self.link_ids_by_pair = {
            frozenset((link["a"], link["b"])): link["id"]
            for link in self.topology.get("links", [])
        }
        for other, node in self.topology.get("nodes", {}).items():
            self.node_endpoints[other] = {"host": "127.0.0.1", "app_port": int(node["app_port"]),
                                          "http_url": f"http://127.0.0.1:{int(node['http_port'])}"}
        for link in self.topology.get("links", []):
            a, b = link["a"], link["b"]
            if self.own_id not in (a, b):
                continue
            other = b if a == self.own_id else a
            node = self.topology["nodes"][other]
            self.peer_endpoints[other] = {
                "host": "127.0.0.1", "app_port": int(node["app_port"]),
                "http_url": f"http://127.0.0.1:{int(node['http_port'])}",
                "link_id": link["id"], "distance_km": float(link["distance_km"]),
            }
        self.kmes = {peer: KeyManager(self.own_id, peer) for peer in self.peer_endpoints}
        if not self.kmes:
            self.kmes = {self.peer_id: KeyManager(self.own_id, self.peer_id)}
        self.kme = self.kmes.get(self.peer_id, next(iter(self.kmes.values())))
        self.end_to_end_keys: dict[str, bytes] = {}
        self.link_up = True
        self._lock = threading.RLock()
        self._http: NodeHTTPServer | None = None
        self._tcp: AppTCPServer | None = None
        self._sequence = 0
        self._session_id = uuid.uuid4().hex
        self._seen_ids: set[tuple[str, str]] = set()
        self._last_sequence: dict[str, int] = {}
        self._outcomes: dict[tuple[str, str], tuple[int, str, bytes]] = {}
        self._inbox: list[dict[str, str]] = []

    def set_link(self, up: bool) -> None:
        with self._lock:
            self.link_up = up

    def is_link_up(self, peer_id: str | None = None) -> bool:
        if self.coordinator_url:
            try:
                suffix = "" if peer_id not in self.peer_endpoints else "?link_id=" + urllib.parse.quote(self.peer_endpoints[peer_id]["link_id"])
                state = json_request(self.coordinator_url + "/api/link-state" + suffix, "GET")
                return bool(state.get("up"))
            except RuntimeError:
                return False
        with self._lock:
            return self.link_up

    def kme_for(self, peer_id: str | None = None) -> KeyManager:
        peer = peer_id or self.peer_id
        try:
            return self.kmes[peer]
        except KeyError as exc:
            raise KMEError(404, "Unknown peer_id.") from exc

    def path_is_up(self, path_text: str) -> bool:
        path = path_text.split(">")
        if len(path) < 2:
            return True
        for left, right in zip(path, path[1:]):
            link_id = self.link_ids_by_pair.get(frozenset((left, right)))
            if link_id is None:
                return False
            if not self.coordinator_url:
                return self.link_up
            try:
                if not json_request(self.coordinator_url + "/api/link-state?link_id=" +
                                    urllib.parse.quote(link_id), "GET").get("up"):
                    return False
            except RuntimeError:
                return False
        return True

    def _key_api_url(self, path: str) -> str:
        # Node apps use their own REST API, even though the API and app share a process.
        return f"http://127.0.0.1:{self.http_port}{path}"

    def get_enc_key_via_api(self, reservation: str, peer_id: str | None = None) -> tuple[str, bytes]:
        route = f"/api/node/reserve-enc?reservation={urllib.parse.quote(reservation)}&peer_id={urllib.parse.quote(peer_id or self.peer_id)}"
        response = json_request(self._key_api_url(route), "POST", {})
        item = response["keys"][0]
        return item["key_ID"], base64.b64decode(item["key"], validate=True)

    def get_dec_key_via_api(self, key_id: str, reservation: str, peer_id: str | None = None) -> bytes:
        query = urllib.parse.urlencode({"key_ID": key_id, "reservation": reservation, "peer_id": peer_id or self.peer_id})
        route = f"/api/node/reserve-dec?{query}"
        response = json_request(self._key_api_url(route), "GET")
        return base64.b64decode(response["keys"][0]["key"], validate=True)

    def finish_key(self, key_id: str, reservation: str, action: str, peer_id: str | None = None) -> None:
        json_request(self._key_api_url("/api/node/finish"), "POST",
                     {"key_ID": key_id, "reservation": reservation, "action": action, "peer_id": peer_id or self.peer_id})

    def send_message(self, text: str, path: list[str] | None = None) -> dict[str, Any]:
        if self.role != "alice":
            raise RuntimeError("Only the Alice node accepts outgoing messages.")
        if not self.is_link_up():
            raise RuntimeError("Simulated inter-node link is down.")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Message must contain 1 to 2,000 characters.")
        if self.topology:
            self.topology["selected_path"] = path
            return self._send_relay_message(text)
        if not self.peer_host or not self.peer_port:
            raise RuntimeError("Alice has no Bob application endpoint configured.")

        message_id = secrets.token_hex(16).upper()
        self._sequence += 1
        sequence = self._sequence
        reservation = message_id
        key_id, key = self.get_enc_key_via_api(reservation)
        nonce = secrets.token_bytes(12)
        aad = message_aad(key_id, message_id, sequence, self._session_id)
        ciphertext = AESGCM(key).encrypt(nonce, text.encode("utf-8"), aad)
        outgoing = Frame(TYPE_MESSAGE, key_id, nonce, ciphertext, message_id, sequence, self._session_id)
        try:
            with socket.create_connection((self.peer_host, self.peer_port), timeout=SOCKET_TIMEOUT) as sock:
                sock.settimeout(SOCKET_TIMEOUT)
                send_frame(sock, outgoing)
                reply = recv_frame(sock)
        except (OSError, TimeoutError, ProtocolError):
            reply = None
        expected = hmac.new(key, message_id.encode("ascii") + b"\x01", hashlib.sha256).digest()
        valid_ack = bool(reply and reply.message_type == TYPE_ACK and reply.key_id == key_id and
                         reply.message_id == message_id and reply.session_id == self._session_id and
                         reply.payload[:1] == b"\x01" and hmac.compare_digest(reply.payload[1:], expected))
        if valid_ack:
            outcome = 1
        else:
            outcome = self.query_outcome(key_id, message_id, sequence, key)
        if outcome == 1:
            self.finish_key(key_id, reservation, "commit")
        elif outcome == 2:
            self.finish_key(key_id, reservation, "release")
            raise RuntimeError("Bob rejected the message; Alice's key was released.")
        else:
            # Unknown is not proof of non-consumption. Keep it reserved until KME expiry.
            raise RuntimeError("Bob's outcome is unknown; key remains reserved pending reconciliation.")
        return {
            "key_id": key_id,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "ciphertext_bytes": len(ciphertext),
            "status": "delivered",
            "message_id": message_id,
            "remaining_keys": len(self.kme.key_ids()),
        }

    def _send_relay_message(self, text: str) -> dict[str, Any]:
        path = self.topology.get("selected_path")
        if not path or len(path) < 3:
            raise RuntimeError("No available Alice-to-Bob relay path.")
        circuit = secrets.token_hex(16).upper()
        session = ">".join(path)
        end_key = secrets.token_bytes(32)
        link_id, link_key = self.get_enc_key_via_api(circuit, path[1])
        nonce = secrets.token_bytes(12)
        transport = Frame(TYPE_KEY_TRANSPORT, link_id, nonce,
                          AESGCM(link_key).encrypt(nonce, end_key, transport_aad(link_id, circuit, 1, session)),
                          circuit, 1, session)
        try:
            reply = self._exchange(transport, self.peer_endpoints[path[1]])
        except (OSError, TimeoutError, ProtocolError):
            reply = None
        status = reply.payload[:1] if reply else b""
        proof = hmac.new(link_key, b"relay-ack" + circuit.encode("ascii") + status, hashlib.sha256).digest()
        valid_reply = bool(reply and status in (b"\x01", b"\x02") and hmac.compare_digest(reply.payload[1:], proof))
        outcome = status[0] if valid_reply else self._query_hop_outcome(transport, link_key, self.peer_endpoints[path[1]])
        if outcome == 1:
            self.finish_key(link_id, circuit, "commit", path[1])
        elif outcome == 2:
            self.finish_key(link_id, circuit, "release", path[1])
            raise RuntimeError("Relay rejected or could not forward the end-to-end key.")
        else:
            raise RuntimeError("Relay key-transport outcome is unknown; the key remains reserved.")
        e2e_id = "E2E-" + circuit
        e2e_nonce = secrets.token_bytes(12)
        self.end_to_end_keys[circuit] = end_key
        seq = self._sequence + 1
        self._sequence = seq
        aad = message_aad(e2e_id, circuit, seq, session)
        message = Frame(TYPE_MESSAGE, e2e_id, e2e_nonce,
                        AESGCM(end_key).encrypt(e2e_nonce, text.encode("utf-8"), aad),
                        circuit, seq, session)
        bob = self.node_endpoints[path[-1]]
        reply = self._exchange(message, bob)
        expected = hmac.new(end_key, circuit.encode("ascii") + b"\x01", hashlib.sha256).digest()
        if reply.message_type != TYPE_ACK or not hmac.compare_digest(reply.payload[1:], expected):
            raise RuntimeError("Bob did not authenticate the end-to-end message.")
        return {"key_id": e2e_id, "message_id": circuit, "status": "delivered",
                "path": path, "ciphertext_bytes": len(message.payload), "remaining_keys": self.kme.total_count()}

    def _query_hop_outcome(self, original: Frame, key: bytes, endpoint: dict[str, Any]) -> int:
        query = Frame(TYPE_OUTCOME_QUERY, original.key_id, bytes(12), b"",
                      original.message_id, original.sequence, original.session_id)
        try:
            reply = self._exchange(query, endpoint, timeout=1.0)
        except (OSError, TimeoutError, ProtocolError):
            return 0
        status = reply.payload[:1]
        proof = hmac.new(key, b"outcome" + original.message_id.encode("ascii") +
                         original.session_id.encode("ascii") + status, hashlib.sha256).digest()
        if reply.message_type != TYPE_OUTCOME or len(reply.payload) != 33 or not hmac.compare_digest(reply.payload[1:], proof):
            return 0
        return status[0] if status in (b"\x01", b"\x02") else 0

    @staticmethod
    def _exchange(frame: Frame, endpoint: dict[str, Any], *, timeout: float = SOCKET_TIMEOUT) -> Frame:
        with socket.create_connection((endpoint["host"], endpoint["app_port"]), timeout=timeout) as sock:
            sock.settimeout(timeout)
            send_frame(sock, frame)
            return recv_frame(sock)

    def _receive_transport(self, frame: Frame) -> Frame:
        path = frame.session_id.split(">")
        if self.own_id not in path:
            return Frame(TYPE_NACK, frame.key_id, bytes(12), b"Node not on transport path.", frame.message_id, frame.sequence, frame.session_id)
        index = path.index(self.own_id)
        if index == 0:
            return Frame(TYPE_NACK, frame.key_id, bytes(12), b"Invalid relay hop.", frame.message_id, frame.sequence, frame.session_id)
        previous = path[index - 1]
        reservation = "relay:" + frame.message_id
        try:
            key = self.get_dec_key_via_api(frame.key_id, reservation, previous)
            end_key = AESGCM(key).decrypt(frame.nonce, frame.payload,
                                          transport_aad(frame.key_id, frame.message_id, frame.sequence, frame.session_id))
        except (RuntimeError, InvalidTag, ValueError):
            try: self.finish_key(frame.key_id, reservation, "release", previous)
            except RuntimeError: pass
            return Frame(TYPE_NACK, frame.key_id, bytes(12), b"Relay frame rejected.", frame.message_id, frame.sequence, frame.session_id)
        self.finish_key(frame.key_id, reservation, "commit", previous)
        self._outcomes[(frame.session_id, frame.message_id)] = (1, frame.key_id, key)
        if index == len(path) - 1:
            self.end_to_end_keys[frame.message_id] = end_key
            proof = hmac.new(key, b"relay-ack" + frame.message_id.encode("ascii"), hashlib.sha256).digest()
            return Frame(TYPE_ACK, frame.key_id, bytes(12), b"\x01" + proof, frame.message_id, frame.sequence, frame.session_id)
        next_peer = path[index + 1]
        out_reservation = "relay:" + frame.message_id
        try:
            out_id, out_key = self.get_enc_key_via_api(out_reservation, next_peer)
        except (RuntimeError, KMEError):
            proof = hmac.new(key, b"relay-ack" + frame.message_id.encode("ascii") + b"\x01", hashlib.sha256).digest()
            return Frame(TYPE_NACK, frame.key_id, bytes(12), b"\x01" + proof, frame.message_id, frame.sequence, frame.session_id)
        nonce = secrets.token_bytes(12)
        forwarded = Frame(TYPE_KEY_TRANSPORT, out_id, nonce,
                          AESGCM(out_key).encrypt(nonce, end_key,
                              transport_aad(out_id, frame.message_id, frame.sequence + 1, frame.session_id)),
                          frame.message_id, frame.sequence + 1, frame.session_id)
        try:
            reply = self._exchange(forwarded, self.peer_endpoints[next_peer], timeout=1.0)
        except (OSError, TimeoutError, ProtocolError):
            reply = None
        status = reply.payload[:1] if reply else b""
        expected = hmac.new(out_key, b"relay-ack" + frame.message_id.encode("ascii") + status, hashlib.sha256).digest()
        valid = bool(reply and status in (b"\x01", b"\x02") and hmac.compare_digest(reply.payload[1:], expected))
        outcome = status[0] if valid else self._query_hop_outcome(forwarded, out_key, self.peer_endpoints[next_peer])
        if outcome == 1:
            self.finish_key(out_id, out_reservation, "commit", next_peer)
        elif outcome == 2:
            self.finish_key(out_id, out_reservation, "release", next_peer)
        # Unknown remains reserved until the KME reservation timeout; do not guess.
        self.end_to_end_keys[frame.message_id] = end_key
        incoming_status = b"\x01"
        proof = hmac.new(key, b"relay-ack" + frame.message_id.encode("ascii") + incoming_status, hashlib.sha256).digest()
        return Frame(TYPE_ACK if outcome == 1 else TYPE_NACK, frame.key_id, bytes(12), incoming_status + proof,
                     frame.message_id, frame.sequence, frame.session_id)

    def query_outcome(self, key_id: str, message_id: str, sequence: int, key: bytes) -> int:
        query = Frame(TYPE_OUTCOME_QUERY, key_id, bytes(12), b"", message_id, sequence, self._session_id)
        try:
            with socket.create_connection((self.peer_host, self.peer_port), timeout=SOCKET_TIMEOUT) as sock:
                sock.settimeout(SOCKET_TIMEOUT)
                send_frame(sock, query)
                reply = recv_frame(sock)
        except (OSError, TimeoutError, ProtocolError):
            return 0
        status = reply.payload[:1]
        if reply.message_type != TYPE_OUTCOME or reply.key_id != key_id or reply.message_id != message_id or reply.session_id != self._session_id:
            return 0
        expected = hmac.new(key, b"outcome" + message_id.encode("ascii") + self._session_id.encode("ascii") + status, hashlib.sha256).digest()
        if len(reply.payload) != 33 or not hmac.compare_digest(reply.payload[1:], expected):
            return 0
        return status[0] if status and status[0] in (1, 2) else 0

    def receive_message(self, frame: Frame) -> Frame:
        if frame.message_type == TYPE_KEY_TRANSPORT:
            try:
                return self._receive_transport(frame)
            except (RuntimeError, KMEError, OSError, TimeoutError, ProtocolError, ValueError) as exc:
                return Frame(TYPE_NACK, frame.key_id, bytes(12), str(exc).encode()[:128], frame.message_id, frame.sequence, frame.session_id)
        if frame.message_type == TYPE_OUTCOME_QUERY:
            return self._outcome_reply(frame)
        if frame.message_type == TYPE_MESSAGE and self.topology and frame.key_id.startswith("E2E-"):
            if not self.path_is_up(frame.session_id):
                return Frame(TYPE_NACK, frame.key_id, bytes(12), b"A link on the message path is down.",
                             frame.message_id, frame.sequence, frame.session_id)
        if self.role != "bob":
            return Frame(TYPE_NACK, frame.key_id, bytes(12), b"Only Bob accepts incoming messages.", frame.message_id or "invalid", frame.sequence)
        if not self.is_link_up():
            return Frame(TYPE_NACK, frame.key_id, bytes(12), b"Simulated inter-node link is down.", frame.message_id or "invalid", frame.sequence)
        if frame.message_type == TYPE_OUTCOME_QUERY:
            return self._outcome_reply(frame)
        if frame.message_type != TYPE_MESSAGE:
            return Frame(TYPE_NACK, frame.key_id, bytes(12), b"Expected a message frame.", frame.message_id or "invalid", frame.sequence)
        replay_key = (frame.session_id, frame.message_id)
        if not frame.message_id or replay_key in self._seen_ids or frame.sequence <= self._last_sequence.get(frame.session_id, 0):
            return Frame(TYPE_NACK, frame.key_id, bytes(12), b"Replay rejected.", frame.message_id, frame.sequence, frame.session_id)
        reservation = frame.message_id
        relay_key = frame.key_id.startswith("E2E-")
        try:
            if relay_key:
                key = self.end_to_end_keys[frame.message_id]
            else:
                key = self.get_dec_key_via_api(frame.key_id, reservation)
        except (RuntimeError, KeyError) as exc:
            return Frame(TYPE_NACK, frame.key_id, bytes(12), str(exc).encode("utf-8")[:512], frame.message_id, frame.sequence, frame.session_id)
        try:
            plaintext = AESGCM(key).decrypt(frame.nonce, frame.payload,
                                            message_aad(frame.key_id, frame.message_id, frame.sequence, frame.session_id))
        except (InvalidTag, ValueError):
            if not relay_key: self.finish_key(frame.key_id, reservation, "release")
            self._outcomes[replay_key] = (2, frame.key_id, key)
            return self._signed_reply(TYPE_NACK, frame, 2, key)
        if not relay_key: self.finish_key(frame.key_id, reservation, "commit")
        self._seen_ids.add(replay_key)
        self._last_sequence[frame.session_id] = frame.sequence
        self._outcomes[replay_key] = (1, frame.key_id, key)
        self._inbox.append({"message_id": frame.message_id, "text": plaintext.decode("utf-8", "replace")})
        return self._signed_reply(TYPE_ACK, frame, 1, key)

    def _signed_reply(self, typ: int, frame: Frame, status: int, key: bytes) -> Frame:
        status_byte = bytes([status])
        proof = hmac.new(key, frame.message_id.encode("ascii") + status_byte, hashlib.sha256).digest()
        return Frame(typ, frame.key_id, bytes(12), status_byte + proof,
                     frame.message_id, frame.sequence, frame.session_id)

    def _outcome_reply(self, frame: Frame) -> Frame:
        known = self._outcomes.get((frame.session_id, frame.message_id))
        if known is not None and known[1] == frame.key_id:
            return self._outcome_signed(frame, known[0], known[2])
        path = frame.session_id.split(">")
        peer_id = path[path.index(self.own_id) - 1] if self.own_id in path and path.index(self.own_id) > 0 else self.peer_id
        try:
            key = self.get_dec_key_via_api(frame.key_id, "outcome:" + frame.message_id, peer_id)
        except RuntimeError:
            return Frame(TYPE_OUTCOME, frame.key_id, bytes(12), b"\x00" + bytes(32),
                         frame.message_id, frame.sequence, frame.session_id)
        self.finish_key(frame.key_id, "outcome:" + frame.message_id, "release", peer_id)
        self._outcomes[(frame.session_id, frame.message_id)] = (2, frame.key_id, key)
        return self._outcome_signed(frame, 2, key)

    def _outcome_signed(self, frame: Frame, status: int, key: bytes) -> Frame:
        status_byte = bytes([status])
        proof = hmac.new(key, b"outcome" + frame.message_id.encode("ascii") +
                         frame.session_id.encode("ascii") + status_byte, hashlib.sha256).digest()
        return Frame(TYPE_OUTCOME, frame.key_id, bytes(12), status_byte + proof,
                     frame.message_id, frame.sequence, frame.session_id)


class NodeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], runtime: NodeRuntime):
        super().__init__(address, NodeHandler)
        self.runtime = runtime


class AppTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], runtime: NodeRuntime):
        super().__init__(address, AppTCPHandler)
        self.runtime = runtime


class AppTCPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(SOCKET_TIMEOUT)
        try:
            frame = recv_frame(self.request)
            response = self.server.runtime.receive_message(frame)  # type: ignore[attr-defined]
        except (ProtocolError, OSError, TimeoutError):
            response = Frame(TYPE_NACK, "invalid", bytes(12), b"Malformed, truncated, or timed-out frame.", "invalid")
        try:
            send_frame(self.request, response)
        except (OSError, ProtocolError):
            pass


class NodeHandler(BaseHTTPRequestHandler):
    server: NodeHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        # Request bodies can carry keys on the key API; never log body or key material.
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.path.split("?", 1)[0] == "/api/node/inbox":
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8765")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise KMEError(400, "Invalid Content-Length.") from exc
        if not 0 < length <= 65_536:
            raise KMEError(400, "Request body is empty or too large.")
        try:
            result = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise KMEError(400, "Request body must be valid JSON.") from exc
        if not isinstance(result, dict):
            raise KMEError(400, "Request body must be a JSON object.")
        return result

    def _route(self, method: str, payload: dict[str, Any] | None = None) -> None:
        route = urllib.parse.urlsplit(self.path)
        parts = route.path.strip("/").split("/")
        runtime = self.server.runtime
        try:
            protected = route.path.startswith("/api/node/") and route.path not in (
                "/api/node/keys", "/api/node/inbox")
            if len(parts) == 5 and parts[:3] == ["api", "v1", "keys"] and parts[4] in ("enc_keys", "dec_keys"):
                protected = True
            if protected and not runtime.insecure_dev:
                supplied = self.headers.get("Authorization", "")
                expected = "Bearer " + (runtime.provision_token or "")
                if not runtime.provision_token or not hmac.compare_digest(
                        supplied.encode("utf-8"), expected.encode("utf-8")):
                    raise KMEError(403, "Valid provisioning token required.")
            if len(parts) == 5 and parts[:3] == ["api", "v1", "keys"]:
                peer_id, operation = parts[3], parts[4]
                if operation == "status" and method == "GET":
                    return self._send(200, runtime.kme_for(peer_id).status(peer_id))
                if operation == "enc_keys" and method in ("GET", "POST"):
                    if not runtime.is_link_up(peer_id): raise KMEError(503, "Simulated link is down.")
                    key_id, material = runtime.kme_for(peer_id).enc_key(peer_id)
                    return self._send(200, {"keys": [{"key_ID": key_id,
                                                       "key": base64.b64encode(material).decode("ascii")}]})
                if operation == "dec_keys" and method in ("GET", "POST"):
                    if not runtime.is_link_up(peer_id): raise KMEError(503, "Simulated link is down.")
                    query = urllib.parse.parse_qs(route.query)
                    key_ids = query.get("key_ID", [])
                    if not key_ids or not key_ids[0]:
                        raise KMEError(400, "Query parameter key_ID is required.")
                    material = runtime.kme_for(peer_id).dec_key(peer_id, key_ids[0])
                    return self._send(200, {"keys": [{"key_ID": key_ids[0],
                                                       "key": base64.b64encode(material).decode("ascii")}]})

            if route.path == "/api/node/keys" and method == "GET":
                peer = urllib.parse.parse_qs(route.query).get("peer_id", [runtime.peer_id])[0]
                kme = runtime.kme_for(peer)
                ids = kme.available_key_ids()
                return self._send(200, {"key_ids": ids, "count": len(ids),
                                        "total_key_count": kme.total_count(),
                                        "limit": kme.limit, "link_up": runtime.is_link_up(peer), "peer_id": peer})
            if route.path == "/api/node/inbox" and method == "GET":
                return self._send(200, {"label": "Bob's inbox", "messages": list(runtime._inbox)})
            if route.path == "/api/node/reserve-enc" and method == "POST":
                if not runtime.is_link_up(): raise KMEError(503, "Simulated link is down.")
                reservation = urllib.parse.parse_qs(route.query).get("reservation", [""])[0]
                if not reservation: raise KMEError(400, "reservation is required.")
                peer = urllib.parse.parse_qs(route.query).get("peer_id", [runtime.peer_id])[0]
                if not runtime.is_link_up(peer): raise KMEError(503, "Simulated link is down.")
                key_id, material = runtime.kme_for(peer).reserve_enc(peer, reservation)
                return self._send(200, {"keys": [{"key_ID": key_id, "key": base64.b64encode(material).decode("ascii")} ]})
            if route.path == "/api/node/reserve-dec" and method == "GET":
                query = urllib.parse.parse_qs(route.query)
                key_id, reservation = query.get("key_ID", [""])[0], query.get("reservation", [""])[0]
                peer = query.get("peer_id", [runtime.peer_id])[0]
                if not key_id or not reservation: raise KMEError(400, "key_ID and reservation are required.")
                if not runtime.is_link_up(peer): raise KMEError(503, "Simulated link is down.")
                material = runtime.kme_for(peer).reserve_dec(peer, key_id, reservation)
                return self._send(200, {"keys": [{"key_ID": key_id, "key": base64.b64encode(material).decode("ascii")} ]})
            if route.path == "/api/node/finish" and method == "POST":
                assert payload is not None
                peer = payload.get("peer_id", runtime.peer_id)
                runtime.kme_for(peer).finish(payload["key_ID"], payload["reservation"], commit=payload["action"] == "commit")
                return self._send(200, {"finished": True})
            if route.path == "/api/node/discard" and method == "POST":
                assert payload is not None
                key_id = payload["key_ID"]
                runtime.kme_for(payload.get("peer_id", runtime.peer_id)).discard(key_id)
                return self._send(200, {"discarded": True})
            if route.path == "/api/node/import" and method == "POST":
                assert payload is not None
                peer = payload.get("peer_id", runtime.peer_id)
                if not isinstance(peer, str) or not runtime.is_link_up(peer): raise KMEError(503, "Simulated link is down.")
                rows = payload.get("keys")
                if not isinstance(rows, list):
                    raise KMEError(400, "keys must be an array.")
                decoded: list[tuple[str, bytes]] = []
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("key_ID"), str) or not isinstance(row.get("key"), str):
                        raise KMEError(400, "Each key needs key_ID and base64 key fields.")
                    try:
                        material = base64.b64decode(row["key"], validate=True)
                    except ValueError as exc:
                        raise KMEError(400, "Key must be valid base64.") from exc
                    decoded.append((row["key_ID"], material))
                runtime.kme_for(peer).import_keys(decoded)
                return self._send(200, {"imported": len(decoded)})
            if route.path == "/api/node/remove" and method == "POST":
                assert payload is not None
                ids = payload.get("key_IDs")
                if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
                    raise KMEError(400, "key_IDs must be an array of strings.")
                runtime.kme_for(payload.get("peer_id", runtime.peer_id)).remove_keys(ids)
                return self._send(200, {"removed": len(ids)})
            if route.path == "/api/node/link" and method == "POST":
                assert payload is not None
                if not isinstance(payload.get("up"), bool):
                    raise KMEError(400, "up must be a boolean.")
                runtime.set_link(payload["up"])
                return self._send(200, {"link_up": runtime.is_link_up()})
            if route.path == "/api/node/reset" and method == "POST":
                for kme in runtime.kmes.values(): kme.reset()
                runtime.set_link(True)
                return self._send(200, {"reset": True})
            if route.path == "/api/app/messages" and method == "POST":
                if runtime.role != "alice":
                    raise KMEError(404, "Endpoint not found.")
                if not runtime.is_link_up():
                    raise KMEError(503, "Simulated inter-node link is down.")
                assert payload is not None
                result = runtime.send_message(payload.get("text"), payload.get("path"))
                return self._send(200, result)
            self._send(404, {"error": "Endpoint not found."})
        except KMEError as exc:
            self._send(exc.status, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except RuntimeError as exc:
            status = 503 if "unreachable" in str(exc).lower() or "link is down" in str(exc).lower() else 409
            self._send(status, {"error": str(exc)})
        except (OSError, TimeoutError, ProtocolError):
            self._send(503, {"error": "A relay or peer node is unavailable."})
        except (KeyError, TypeError, OverflowError):
            self._send(400, {"error": "Invalid request fields."})

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8765")
        self.send_header("Access-Control-Allow-Methods", "GET")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        payload = {}
        try:
            has_body = int(self.headers.get("Content-Length", "0") or "0") > 0
        except ValueError:
            return self._send(400, {"error": "Invalid Content-Length."})
        if has_body:
            try:
                payload = self._body()
            except KMEError as exc:
                return self._send(exc.status, {"error": str(exc)})
        self._route("POST", payload)


def run_node(runtime: NodeRuntime) -> None:
    http_server = NodeHTTPServer((runtime.bind_host, runtime.http_port), runtime)
    runtime.http_port = http_server.server_port
    tcp_server = AppTCPServer((runtime.app_host, runtime.app_port), runtime)
    runtime.app_port = int(tcp_server.server_address[1])
    runtime._http, runtime._tcp = http_server, tcp_server
    threading.Thread(target=tcp_server.serve_forever, daemon=True, name=f"{runtime.role}-app-tcp").start()
    print(f"{runtime.role.title()} KME REST listening at http://{runtime.bind_host}:{runtime.http_port}")
    print(f"{runtime.role.title()} application TCP listening at {runtime.app_host}:{runtime.app_port}")
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        print(f"Stopping {runtime.role} node…")
    finally:
        tcp_server.shutdown()
        http_server.server_close()
        tcp_server.server_close()


def resolve_app_port(topology: dict[str, Any] | None, node_id: str,
                     http_port: int, app_port_override: int | None = None) -> int:
    if app_port_override is not None:
        return app_port_override
    if topology is not None and node_id in topology.get("nodes", {}):
        return int(topology["nodes"][node_id]["app_port"])
    return http_port + 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one QKD application node and its local KME.")
    parser.add_argument("--role", required=True, choices=("alice", "bob", "relay"))
    parser.add_argument("--node-id", help="Topology node ID (e.g. r1 or r2).")
    parser.add_argument("--topology", help="Topology JSON file; enables per-neighbor KMEs and relay behavior.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: localhost).")
    parser.add_argument("--port", type=int, help="KME REST HTTP port (default: 8766 Alice, 8767 Bob).")
    parser.add_argument("--app-host", default="127.0.0.1", help="TCP app bind host.")
    parser.add_argument("--app-port", type=int, help="TCP messaging port (default: REST port + 2).")
    parser.add_argument("--peer", help="Alice's Bob endpoint as HOST:PORT (Bob's TCP app port).")
    parser.add_argument("--coordinator", default="http://127.0.0.1:8765", help="Coordinator link-state endpoint.")
    parser.add_argument("--insecure-dev", action="store_true", help="Explicitly disable provisioning-token checks for isolated tests.")
    args = parser.parse_args()
    if args.host != "127.0.0.1" or args.app_host != "127.0.0.1":
        parser.error("This prototype binds to 127.0.0.1 only.")
    if urllib.parse.urlsplit(args.coordinator).hostname != "127.0.0.1":
        parser.error("The coordinator must be on 127.0.0.1.")
    if not os.getenv("QKD_PROVISION_TOKEN") and not args.insecure_dev:
        parser.error("Set QKD_PROVISION_TOKEN or pass --insecure-dev.")
    topology = None
    node_id = args.node_id or args.role
    if args.topology:
        try:
            topology = load_topology(args.topology)
            node = topology["nodes"].get(node_id)
            if node is None:
                parser.error(f"Node ID {node_id!r} is not present in the topology.")
            if node["role"] != args.role:
                parser.error("--role does not match the selected topology node.")
        except (TopologyError, KeyError, TypeError) as exc:
            parser.error(f"Invalid topology file or node ID: {exc}")
    default_http = int(topology["nodes"][node_id]["http_port"]) if topology else (8766 if args.role == "alice" else 8767)
    http_port = args.port or default_http
    app_port = resolve_app_port(topology, node_id, http_port, args.app_port)
    peer_host, peer_port = (None, None)
    if args.peer:
        try:
            peer_host, port = args.peer.rsplit(":", 1)
            peer_port = int(port)
            if peer_host != "127.0.0.1":
                parser.error("Peer nodes must be on 127.0.0.1.")
        except ValueError as exc:
            parser.error("--peer must be HOST:PORT")
    run_node(NodeRuntime(args.role, args.host, http_port, args.app_host,
                         app_port, peer_host, peer_port, insecure_dev=args.insecure_dev,
                         coordinator_url=args.coordinator, node_id=node_id, topology=topology))


if __name__ == "__main__":
    main()

"""Length-prefixed, bounded TCP application protocol for Alice-to-Bob messages."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

VERSION = 1
TYPE_MESSAGE = 1
TYPE_ACK = 2
TYPE_NACK = 3
MAX_PAYLOAD = 16_384
MAX_KEY_ID = 64
MAX_FRAME = 6 + MAX_KEY_ID + 12 + 4 + MAX_PAYLOAD
_PREFIX = struct.Struct("!I")
_META = struct.Struct("!BBH")  # version, type, key_ID byte length
_LENGTH = struct.Struct("!I")  # payload length, after key_ID and nonce


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    message_type: int
    key_id: str
    nonce: bytes
    payload: bytes


def encode_frame(frame: Frame) -> bytes:
    try:
        key_bytes = frame.key_id.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ProtocolError("key_ID must be ASCII.") from exc
    if not 1 <= len(key_bytes) <= MAX_KEY_ID:
        raise ProtocolError("Invalid key_ID length.")
    if len(frame.nonce) != 12:
        raise ProtocolError("Nonce must be 12 bytes.")
    if len(frame.payload) > MAX_PAYLOAD:
        raise ProtocolError("Payload exceeds maximum size.")
    if frame.message_type not in (TYPE_MESSAGE, TYPE_ACK, TYPE_NACK):
        raise ProtocolError("Unknown message type.")
    body = _META.pack(VERSION, frame.message_type, len(key_bytes))
    body += key_bytes + frame.nonce + _LENGTH.pack(len(frame.payload)) + frame.payload
    return _PREFIX.pack(len(body)) + body


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        block = sock.recv(size - len(chunks))
        if not block:
            raise ProtocolError("Truncated TCP frame.")
        chunks.extend(block)
    return bytes(chunks)


def recv_frame(sock: socket.socket) -> Frame:
    frame_size = _PREFIX.unpack(recv_exact(sock, _PREFIX.size))[0]
    if frame_size < _META.size + 1 + 12 + _LENGTH.size or frame_size > MAX_FRAME:
        raise ProtocolError("Invalid or oversized TCP frame.")
    metadata = recv_exact(sock, _META.size)
    version, message_type, key_len = _META.unpack(metadata)
    if version != VERSION:
        raise ProtocolError("Unsupported protocol version.")
    if message_type not in (TYPE_MESSAGE, TYPE_ACK, TYPE_NACK):
        raise ProtocolError("Unknown message type.")
    if not 1 <= key_len <= MAX_KEY_ID:
        raise ProtocolError("Invalid frame field length.")
    try:
        key_id = recv_exact(sock, key_len).decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError("key_ID must be ASCII.") from exc
    nonce = recv_exact(sock, 12)
    payload_len = _LENGTH.unpack(recv_exact(sock, _LENGTH.size))[0]
    if payload_len > MAX_PAYLOAD:
        raise ProtocolError("Invalid or oversized payload.")
    expected = _META.size + key_len + 12 + _LENGTH.size + payload_len
    if expected != frame_size:
        raise ProtocolError("Frame lengths do not match.")
    payload = recv_exact(sock, payload_len)
    return Frame(message_type, key_id, nonce, payload)


def send_frame(sock: socket.socket, frame: Frame) -> None:
    sock.sendall(encode_frame(frame))

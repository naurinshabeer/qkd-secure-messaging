# TCP application protocol (version 1)

This document specifies the application frame exchanged between Alice and Bob. It is a teaching protocol for this repository, not a standardized wire protocol. Integers are unsigned and big-endian. TCP is a byte stream, so every frame is prefixed with its body length and receivers must read exact field lengths; one `recv()` call is not assumed to return a whole frame.

## Frame layout

The 4-byte length prefix is not counted in the frame-body length. The payload length counts payload bytes only. For a message frame, the payload is the AES-256-GCM ciphertext with its 16-byte authentication tag appended by `AESGCM.encrypt`.

| Offset (from body start) | Size | Field | Encoding / meaning |
|---:|---:|---|---|
| 0 | 1 byte | Version | `0x01` |
| 1 | 1 byte | Message type | `0x01` message, `0x02` ACK, `0x03` NACK |
| 2 | 2 bytes | Key ID length | Unsigned big-endian; 1–64 bytes |
| 4 | variable | Key ID | ASCII, e.g. `QK-<uppercase UUID hex>` |
| 4 + ID length | 12 bytes | Nonce | AES-GCM nonce. ACK/NACK use twelve zero bytes. |
| 16 + ID length | 4 bytes | Payload length | Unsigned big-endian; maximum 16,384 bytes |
| 20 + ID length | variable | Payload | Ciphertext+tag for type 1; UTF-8 receipt/error for ACK/NACK |

A frame on the wire is:

```text
+--------------------------+------------------------------+
| body length: uint32 BE   | body (length bytes)          |
+--------------------------+------------------------------+
```

The minimum body size is 23 bytes (one-byte key ID). The maximum body size is 16,470 bytes (64-byte key ID and 16,384-byte payload). The implementation rejects frames outside those limits, unknown versions/types, non-ASCII or invalid-length IDs, inconsistent lengths, and oversized payloads. It closes/responds to malformed connections without terminating the listening server. A peer that stalls mid-frame is subject to the 3-second socket timeout.

## Message types

### 0x01 — Alice to Bob message

The payload is UTF-8 plaintext encrypted with AES-256-GCM. The selected 32-byte key is fetched from Alice's `enc_keys` KME route. The key ID's ASCII bytes are used as AES-GCM associated data, so changing the ID invalidates authentication. Alice consumes her KME copy before attempting the TCP send.

### 0x02 — Bob ACK

Bob sends this only after retrieving and consuming the matching key from Bob's `dec_keys` API, successfully authenticating/decrypting, and decoding the UTF-8 plaintext. The ACK payload contains that plaintext as the local teaching dashboard receipt. The ACK frame uses the same key ID and a zero nonce. Since the ACK is plain TCP data, it is visible to anyone who can capture the lab network; this is not a production messaging design.

### 0x03 — Bob NACK

Bob returns a short UTF-8 error for a known protocol/application failure, such as link down, unknown/consumed key, malformed message type, or GCM authentication failure. Error payloads are limited to 512 bytes by the receiver. On truncated connections where a frame cannot be parsed, the receiver attempts a NACK with key ID `invalid`; if the socket is already closed, it simply closes the handler.

## Failure and key-consumption behavior

| Condition | Result |
|---|---|
| Alice has no key | KME returns HTTP 409; no TCP frame is sent. |
| Bob does not know the key ID | Bob returns NACK after a 404 from his KME. |
| Bob already consumed that key | Bob returns NACK after a 409 from his KME. |
| Ciphertext or authenticated key ID is changed | AES-GCM raises `InvalidTag`; Bob returns NACK. Bob's key is already consumed. |
| Alice cannot reach Bob / connect timeout | Alice's reserved key remains consumed; coordinator reports delivery failure. |
| Truncated, oversized, bad-version or malformed frame | Handler rejects the frame; server process keeps listening. |
| Simulated inter-node link is down | Alice does not connect and Bob rejects inbound frames. |

An interruption after Alice consumes a key can leave Alice and Bob's available counts temporarily different. The coordinator reports per-node counts. Reset clears both pools. There is no automatic protocol for resynchronizing orphaned keys in this milestone.


# TCP application protocol (version 2)

The educational message channel uses a length-prefixed TCP frame. Integers are unsigned big-endian. TCP is a byte stream; readers must loop until each field is complete. This is a project-specific format, not a standard and not a secure production protocol.

## Byte layout

The first four bytes are the body length and are not included in the value. The body is:

| Body offset | Size | Field | Meaning |
|---:|---:|---|---|
| 0 | 1 | Version | `0x01` |
| 1 | 1 | Type | `1` message, `2` ACK, `3` NACK |
| 2 | 2 | Key ID length | unsigned big-endian |
| 4 | 2 | Message ID length | unsigned big-endian |
| 6 | 8 | Sequence | unsigned big-endian |
| 14 | variable | Key ID | ASCII |
| 14 + key ID length | variable | Message ID | ASCII |
| following | 12 | Nonce | AES-GCM nonce (zero for receipt frames) |
| following | 4 | Payload length | unsigned big-endian |
| following | variable | Payload | ciphertext + GCM tag, ACK proof, or short NACK reason |

The outer prefix is `uint32 body_length`. The maximum payload is 16,384 bytes. Message AES-GCM associated data is the ASCII key ID. Alice reserves a matching key by message ID. Bob reserves his copy, authenticates the ciphertext, and only commits after successful GCM verification. A failure releases Bob's reservation. Alice commits only after verifying Bob's ACK; a failed delivery releases Alice's reservation and asks Bob to discard the matching copy.

## ACK and replay behavior

An ACK contains no plaintext. Its payload is status byte `0x01` followed by HMAC-SHA256 over `message_ID || status`, keyed directly by the message key in this teaching prototype. Alice verifies the HMAC before committing her reservation. Bob stores decrypted content only in his in-memory inbox, available at `GET /api/node/inbox` (labelled “Bob's inbox” in the dashboard).

Bob rejects a duplicate message ID and any sequence number not greater than the last accepted sequence for the current process. Replay history is in-memory and resets when Bob restarts. This is not persistent replay protection.

## Failures and limitations

Truncated, oversized, malformed, timed-out, unknown-key, replayed, or GCM-invalid frames are rejected without terminating the listener. A GCM failure releases the reservation rather than burning Bob's available key. The two-phase flow reduces pool drift; a process crash between commit/rollback calls can still leave uncertain state. Key IDs and pending reservations are in-memory.

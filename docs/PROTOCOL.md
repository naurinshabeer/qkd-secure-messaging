# TCP application protocol (version 3)

Messages use a bounded length-prefixed TCP frame. Integers are unsigned big-endian. This is a project protocol, not a standard or production security design.

## Byte layout

The first four bytes hold `body_length` and are not included in the length. The body uses this layout for every frame:

| Body offset | Size | Field | Meaning |
|---:|---:|---|---|
| 0 | 1 | Version | `0x01` |
| 1 | 1 | Type | `1` message, `2` ACK, `3` NACK, `4` outcome query, `5` outcome result |
| 2 | 2 | Key ID length | unsigned big-endian |
| 4 | 2 | Message ID length | unsigned big-endian |
| 6 | 2 | Session ID length | unsigned big-endian |
| 8 | 8 | Sequence | unsigned big-endian |
| 16 | variable | Key ID | ASCII |
| following | variable | Message ID | ASCII |
| following | variable | Session ID | ASCII |
| following | 12 | Nonce | AES-GCM nonce; zero in control frames |
| following | 4 | Payload length | unsigned big-endian |
| following | variable | Payload | ciphertext+tag, status+HMAC, or short NACK detail |

The maximum payload is 16,384 bytes. Message AES-GCM associated data is `key_ID || 0x00 || message_ID || 0x00 || uint64(sequence) || 0x00 || session_ID`, with IDs encoded as ASCII. Alice creates a random session ID at startup and increments its sequence counter per message, so a restarted Alice can start at 1 under a fresh session.

## ACK and outcome query

An ACK contains no plaintext. Its payload is status byte `0x01` followed by HMAC-SHA256 over `message_ID || status`, keyed with the message key. Alice verifies with `hmac.compare_digest` before committing her reserved key. Bob keeps decrypted text only at `GET /api/node/inbox` (“Bob's inbox”).

If an ACK is absent or invalid, Alice reconnects and sends type `0x04` with the same key ID, message ID, session ID and sequence. Bob answers with type `0x05`, one status byte (`1` committed, `2` rejected, `0` unknown), and HMAC-SHA256 over `"outcome" || message_ID || session_ID || status`. Alice commits only on a valid signed `1`, releases only on a valid signed `2`, and does not release on unknown or unverifiable results. Bob caches outcomes in memory so repeated queries are idempotent.

Bob rejects a duplicate `(session_ID, message_ID)` or a sequence not greater than the last accepted sequence for that session. Reservations expire after 30 seconds. Replay/outcome state is in memory; consumed KME IDs still prevent message keys from being reused after Bob restarts.

## Failure cases and resolution

| Failure | Resolution |
|---|---|
| Bob committed; ACK was dropped | Alice queries; signed `committed` makes Alice commit her copy. |
| Bob rejected GCM authentication | Bob releases his reservation and returns signed `rejected`; Alice releases only after verifying it. |
| ACK is invalid or tampered | Alice queries outcome; does not trust the ACK. |
| Query returns unknown, times out, or has bad HMAC | Alice keeps her key reserved; KME expires it after 30 seconds. |
| Alice restarts | A fresh random session ID allows the sequence counter to restart safely. |
| Truncated, oversized or malformed frame | Connection is rejected; listener stays running. |

The protocol is educational. It has no persistent transaction journal, durable replay store, finite-key proof, privacy amplification, or real quantum hardware.

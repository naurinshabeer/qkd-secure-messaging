# TCP application protocol (version 1)

Messages use a bounded length-prefixed TCP frame. Integers are unsigned big-endian. This is a project protocol, not a standard or production security design.

## Byte layout

The first four bytes hold `body_length` and are not included in the length. The body uses this layout for every frame:

| Body offset | Size | Field | Meaning |
|---:|---:|---|---|
| 0 | 1 | Version | `0x01` |
| 1 | 1 | Type | `1` message, `2` ACK, `3` NACK, `4` outcome query, `5` outcome result, `6` relay key transport |
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

## Trusted-relay key transport

The coordinator creates different key IDs for each configured link and imports each ID and its matching 256-bit material to the two endpoint KMEs for that link. Alice creates a random end-to-end key `K` and a transport `message_ID` (the circuit ID). Each relay hop reserves the sender's outgoing and receiver's matching key under that ID. The sender wraps `K` with AES-256-GCM using that hop's link key, then sends type `0x06` to the next node over TCP. The relay decrypts and authenticates the frame before committing its inbound key; it then wraps `K` under the next link's key. Bob stores `K`, and Alice encrypts the application message end-to-end with it. The relay is **trusted** and sees `K` in memory.

The transport frame uses the same byte layout above. For type `0x06`, `key_ID` identifies the one-time key for the current physical/simulated hop, `message_ID` is the end-to-end circuit identifier, `session_ID` lists the path as ASCII node IDs separated by `>`, `sequence` increments at each hop, `nonce` is a fresh 12-byte AES-GCM nonce, and `payload` is the wrapped 32-byte end-to-end key plus the GCM tag. Associated data is `"QKD-RELAY\0" || key_ID || 0x00 || message_ID || 0x00 || uint64(sequence) || 0x00 || session_ID`. ACK payloads contain status `0x01` and HMAC-SHA256 over `"relay-ack" || message_ID || status`, keyed with that hop's link key. No key material appears in an ACK or browser response.

If a hop reply is lost or invalid, the sender uses type `0x04` to query that receiver's in-memory outcome for the same key ID, circuit ID, path and sequence. Type `0x05` returns status `1` committed, `2` rejected/released, or `0` unknown, authenticated with the link key. The sender commits only when it can verify that the receiver committed, releases only when the receiver confirms rejection, and leaves an unknown reservation to the existing 30-second expiry. Relays apply this transaction independently to their incoming and outgoing link pools. A failure after a relay committed its incoming hop does not roll that hop back; its sender commits that hop while the next-hop key is independently released or kept reserved pending reconciliation.

## Topology and failover

`config/topology.json` lists nodes and links. Each link specifies a unique ID, endpoint IDs, distance in kilometres, channel noise, Eve interception fraction, and initial up/down state. The coordinator runs SeQUeNCe separately for each available link and owns one simulated `LinkState` per link. It chooses only paths whose every link is up and whose two endpoint pools each contain a key, then picks the path with the lowest total distance. If neither configured route is usable, the coordinator returns an unavailable-path error. The path selected for a successful message is returned to the dashboard.

These relay frames and path semantics are project-specific, not a standard protocol. The model uses in-memory state and synchronous localhost sockets, and the relay is a trusted security boundary.

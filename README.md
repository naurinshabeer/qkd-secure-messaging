# Relay: SeQUeNCe QKD Network and Secure Messaging Prototype

Relay is a computer-networks course prototype with separate Alice and Bob node processes. Each node owns an independent in-memory Key Management Entity (KME), exposes a small key-delivery REST API, and runs an application endpoint. A third coordinator runs the SeQUeNCe link simulation, provisions matching simulator key material to both KMEs, and serves the dashboard.

> **Scope and security:** The quantum channel and authenticated classical provisioning relationship are simulated; no photons or QKD hardware are used. This project does not provide a finite-key security proof or privacy amplification and is not a real-world secure deployment. It is a teaching prototype; do not use it or its keys to protect real information.

## Architecture

```text
 Browser
    │ HTTP dashboard/API :8765
    ▼
 Link coordinator ───── runs SeQUeNCe BB84 + Cascade once
    │                   creates one key_ID per matching key
    ├──── HTTP provision same key_ID + material ────► Alice KME :8766
    └──── HTTP provision same key_ID + material ────► Bob KME   :8767
                                                           │
 Alice application ─── TCP AES-GCM frame ────────────────► Bob application :8769
       │                                                       │
       └── Alice KME REST :8766           Bob KME REST :8767 ──┘
```

The coordinator contains no application key pool. It temporarily holds SeQUeNCe output while provisioning it to both nodes. The TCP ACK contains only a status and a short HMAC confirmation, never plaintext. Decrypted messages remain in Bob's process and are exposed by `GET /api/node/inbox`; the dashboard labels this view “Bob's inbox”.

The coordinator simulates one quantum-link run in `qkd_demo/sequence_link.py`. It generates a single key ID for each matching key and posts the same ID/material pair to both KMEs. The coordinator also models the authenticated classical channel needed by BB84: provisioning requests use the shared pre-shared token. The node checks that token in constant time. These are software simulations; no photons travel between operating-system processes.

## Project structure

```text
app.py                         Dashboard/coordinator entry point
qkd_demo/sequence_link.py      SeQUeNCe BB84, Eve channel, Cascade experiment
qkd_demo/key_manager.py        Per-node in-memory KME and single-use key state
qkd_demo/node.py               Alice/Bob REST service and TCP application endpoint
qkd_demo/protocol.py           Bounded length-prefixed TCP frames
qkd_demo/coordinator.py        Simulator orchestration, key provisioning, dashboard API
web/                           Dashboard HTML, CSS and JavaScript
docs/PROTOCOL.md               TCP wire format and examples
tests/                         Simulator, KME, protocol and network integration tests
Dockerfile
docker-compose.yml
```

There is no database. Keys and experiment state are in memory and disappear when their processes stop or reset. The coordinator only displays Alice's available pool and Bob's available count; key bytes never enter the browser response.

## Key-delivery API

The node REST API is inspired by **ETSI GS QKD 014** paths and field names. This project is **modelled on** the standard; it is not ETSI-compliant. It simplifies authentication, key batch negotiation, TLS, endpoint discovery, error schemas, and lifecycle handling. Both `GET` and `POST` are accepted for key retrieval to make the lab easy to exercise.

The peer identifier is the remote node: Alice asks for `bob`; Bob asks for `alice`.

| Method and path | Result |
|---|---|
| `GET /api/v1/keys/{peer_id}/status` | JSON status with endpoint IDs and available key count. |
| `GET` or `POST /api/v1/keys/{peer_id}/enc_keys` | Consumes one available local key and returns `{"keys":[{"key_ID":"…","key":"<base64>"}]}`. |
| `GET` or `POST /api/v1/keys/{peer_id}/dec_keys?key_ID=…` | Returns and consumes the local copy for that exact ID. |

Error responses use JSON `{"error":"…"}`. Invalid fields or missing `key_ID` return **400**; unknown peer or unknown key ID returns **404**; empty pool, already-consumed key, duplicate ID, or capacity conflict returns **409**; node/link unavailable returns **503**. A key is reserved by message ID, then committed only after Bob returns a valid authenticated ACK. Failed delivery releases Alice's reservation and tells Bob to discard his copy. Bob verifies GCM before committing his key, so unauthenticated frames do not burn it.

Coordinator-to-KME provisioning uses `/api/node/import` and related internal KME routes. They require `Authorization: Bearer $QKD_PROVISION_TOKEN`; nodes compare the token in constant time. The token must be set in every process environment. `--insecure-dev` explicitly disables this check for isolated local development. No HTTP request body is logged.

## TCP application protocol

Alice's `/api/app/messages` endpoint requests a key from Alice's own REST KME, encrypts with AES-256-GCM, and opens a TCP connection to Bob. Bob's TCP handler parses a bounded frame, checks the link flag, requests the same key ID from Bob's KME, authenticates/decrypts the ciphertext, and returns an ACK or NACK. Alice's endpoint relays only Bob's authenticated status to the coordinator UI. If the ACK is lost or fails verification, Alice asks Bob for a signed, idempotent outcome by message ID before committing or releasing her reservation; an unknown outcome stays reserved until its 30-second expiry. Frame IDs, session ID, and sequence are covered by AES-GCM associated data. The UI reads decrypted text from Bob's labelled inbox endpoint. The wire layout and limits are in [docs/PROTOCOL.md](docs/PROTOCOL.md).

TCP framing uses `recv_exact` and a maximum frame/payload size. Truncated, oversized, unsupported-version, and invalid-length frames are rejected without taking down the listener. Socket connect/read timeouts, unreachable peers, unknown key IDs, and GCM authentication failures are handled as error responses. The coordinator owns the single LinkState and serves `GET /api/link-state`. Nodes consult it before provisioning, reserving keys, or accepting message frames. This shared state is a software simulation of link availability.

## Run locally on Windows (no Docker)

Use Python 3.12. Create/install the project environment once:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Set the same token independently in all three PowerShell windows, then start each command in a separate window. Use a long random value for your local demo: `$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'`. Start Bob first:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role bob --host 127.0.0.1 --port 8767 --app-port 8769 --coordinator http://127.0.0.1:8765
```

Then Alice; `--peer` points to Bob's TCP application port:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role alice --host 127.0.0.1 --port 8766 --app-port 8768 --peer 127.0.0.1:8769 --coordinator http://127.0.0.1:8765
```

Then the link simulator, coordinator, and dashboard:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.coordinator --host 127.0.0.1 --port 8765 --alice http://127.0.0.1:8766 --bob http://127.0.0.1:8767
```

Open `http://127.0.0.1:8765` in a browser on the same computer. Stop each process with Ctrl+C in its own terminal. Every process is restricted to `127.0.0.1`. Both node and coordinator refuse startup without the token unless `--insecure-dev` is explicitly passed. The coordinator must be running before the nodes can provision or deliver messages because each node checks its `/api/link-state` endpoint. Use the same token in every window. `--insecure-dev` is for a completely isolated local experiment only and must be passed to each process.

### Manual REST checks

With the three processes running, inspect status without retrieving any key:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/v1/keys/bob/status
Invoke-RestMethod http://127.0.0.1:8767/api/v1/keys/alice/status
```

Generate keys through the dashboard, or POST `{"count":2}` to `http://127.0.0.1:8765/api/keys/generate`. The coordinator simulates one link run and provisions up to the requested count to both KMEs with shared IDs.

## Docker deployment and packet capture

Docker Compose creates three services on the user-defined bridge network `qkd-lab-net`. Dashboard and node API/application ports are published to localhost for access and capture.

```powershell
docker compose up --build
```

Open `http://127.0.0.1:8765`; stop with Ctrl+C, or in another terminal run `docker compose down`. For packet capture, select the Docker/WSL bridge interface used by your Docker Desktop backend; interface names and host visibility vary by Windows/Docker version. The Alice-to-Bob TCP application frame contains AES-GCM ciphertext. **

## Dashboard walkthrough

1. Start Bob, Alice, and the coordinator. The dashboard should report both nodes online and show two separate pool counts.
2. Leave the default 10 km, 1% noise, and 0% Eve settings. Click **Apply settings**, then **Run SeQUeNCe experiment**.
3. Inspect measured mean QBER, matching Cascade keys, decision, and Cascade rate estimate. The coordinator creates one ID for each imported key and sends the same ID/key pair to both KMEs. Compare Alice and Bob pool counts.
4. Send a short Alice message. Alice consumes her KME copy, sends ciphertext over TCP, and Bob consumes his matching copy and decrypts it. The receipt shown in the UI comes back from Bob's TCP handler. Both available counts decrease by one.
5. Set Eve interception to 100%, apply, and run another simulation. Intercept-resend can raise QBER and trigger the simplified 11% teaching cutoff. Results are stochastic; repeat a run if measurements do not show the expected trend.
6. Increase noise or distance, run again, and compare measured results. The controls are simulator parameters; do not claim calibrated fibre measurements.
7. Use **Simulate link failure** and try sending. Both node application endpoints are told to reject traffic; **Restore link** re-enables them.
8. Consume remaining keys to demonstrate the empty-pool conflict. **Reset session** clears both KME pools and resets the coordinator.

## Tests

Run from this directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use a deterministic SeQUeNCe stub for fast orchestration checks and real fixed-seed SeQUeNCe tests for clean/Eve behavior. Network tests start ephemeral localhost node services and cover shared key IDs/material, single-use APIs, unknown IDs/peers, partial reads, truncated/oversized frames, tampered ciphertext, end-to-end Alice→Bob messaging, link-down, peer failure, and empty pools.

## Course-project scope and remaining work

This milestone demonstrates separate network processes, REST key delivery, TCP framing, per-node key state, simulation-backed key provisioning, and UI-mediated end-to-end message delivery. It does not implement a trusted relay for multi-hop paths, privacy amplification, a real authenticated classical channel, persistent key storage, production KME authentication, or statistical experiment sweeps. It does not claim real-world security.

# Relay: SeQUeNCe QKD Network and Secure Messaging

Relay has separate Alice, two trusted relay, and Bob node processes. Every node owns an in-memory KME for each neighbor, exposes a small key-delivery REST API, and runs a TCP application endpoint. A coordinator runs SeQUeNCe link experiments, provisions each link independently, and serves the dashboard.

## Architecture

```text
                    ┌── R1 ──┐
Browser → Coordinator              Bob
                    └── R2 ──┘
             Four independent links
Alice → {A-R1, R1-B, A-R2, R2-B} → Bob
```

Each edge has two independent KME pools (one at each endpoint), populated from a separate SeQUeNCe BB84 + Cascade run. For each matching key, the coordinator creates one ID and provisions identical ID/material to both ends. It does not have a shared application key pool. Alice creates an end-to-end key `K`; each relay decrypts and re-encrypts `K` with the two adjacent link keys. The relay is **trusted** and sees `K`. The application message is then encrypted end-to-end with `K` and sent to Bob over TCP.

The TCP acknowledgement contains only status and an HMAC confirmation, never plaintext. Bob keeps decrypted text in his process and exposes it from `GET /api/node/inbox`. Provisioning uses a shared pre-shared token checked in constant time.

## Project structure

```text
app.py                         Dashboard/coordinator entry point
config/topology.json           Node ports and per-link settings
qkd_demo/sequence_link.py      SeQUeNCe BB84, Eve channel, Cascade experiment
qkd_demo/key_manager.py        Per-node in-memory KME and single-use key state
qkd_demo/node.py               Alice, relay, and Bob REST/TCP services
qkd_demo/protocol.py           Bounded length-prefixed TCP frames
qkd_demo/coordinator.py        Link orchestration, key provisioning, dashboard API
web/                           Dashboard HTML, CSS and JavaScript
docs/PROTOCOL.md               TCP wire format and examples
tests/                         Link, KME, protocol and network integration tests
```

There is no database. Keys and experiment state are in memory and disappear when their processes stop or reset. The dashboard shows each link's pool count and QBER; raw key bytes never enter browser responses.

## Key-delivery API

The node REST API is inspired by **ETSI GS QKD 014** paths and field names. This project is **modelled on** the standard; it is not ETSI-compliant. It simplifies authentication, key batch negotiation, TLS, endpoint discovery, error schemas, and lifecycle handling. Both `GET` and `POST` are accepted for key retrieval.

The peer identifier is the neighbor on that particular link: for example, Alice asks for `r1`, while Relay 1 asks for `alice` on one KME and `bob` on its other KME.

| Method and path | Result |
|---|---|
| `GET /api/v1/keys/{peer_id}/status` | JSON status with endpoint IDs and available key count. |
| `GET` or `POST /api/v1/keys/{peer_id}/enc_keys` | Consumes one available local key and returns `{"keys":[{"key_ID":"…","key":"<base64>"}]}`. |
| `GET` or `POST /api/v1/keys/{peer_id}/dec_keys?key_ID=…` | Returns and consumes the local copy for that exact ID. |

Error responses use JSON `{"error":"…"}`. Invalid fields or missing `key_ID` return **400**; unknown peer or unknown key ID returns **404**; empty pool, already-consumed key, duplicate ID, or capacity conflict returns **409**; node/link unavailable returns **503**. A key is reserved by message ID, then committed only after Bob returns a valid authenticated ACK. Failed delivery releases Alice's reservation and tells Bob to discard his copy. Bob verifies GCM before committing his key, so unauthenticated frames do not burn it.

Coordinator-to-KME provisioning uses `/api/node/import` and related internal KME routes. They require `Authorization: Bearer $QKD_PROVISION_TOKEN`; nodes compare the token in constant time. The token must be set in every process environment. `--insecure-dev` explicitly disables this check for isolated local development. No HTTP request body is logged.

## TCP application protocol

Alice's `/api/app/messages` endpoint chooses the route supplied by the coordinator, reserves a first-hop key, wraps a fresh end-to-end key in a type `0x06` TCP frame, and sends it to the first relay. Each relay unwraps the key and wraps it with its next-hop key. Hop keys are committed or released independently after authenticated ACK/outcome handling. Alice then encrypts the message end-to-end and sends it to Bob over TCP; Bob decrypts it using the relayed key and returns an ACK with no plaintext. Frame IDs, session ID, sequence, and route are authenticated as associated data. The UI reads decrypted text from Bob's labelled inbox endpoint. The wire layout and limits are in [docs/PROTOCOL.md](docs/PROTOCOL.md).

TCP framing uses `recv_exact` and a maximum frame/payload size. Truncated, oversized, unsupported-version, and invalid-length frames are rejected without taking down the listener. Socket connect/read timeouts, unreachable peers, unknown key IDs, and GCM authentication failures are handled as error responses. The coordinator owns the single LinkState and serves `GET /api/link-state`. Nodes consult it before provisioning, reserving keys, or accepting message frames.

## Run locally on Windows PowerShell

Install dependencies once (Python 3.12):

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Set the same token in each terminal. Start five processes in five PowerShell windows. The coordinator needs to be up before the nodes because each node checks its shared link-state endpoint. Each node reads its own ports and neighbors from `config/topology.json`.

**Terminal 1 — coordinator and dashboard:**

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.coordinator --host 127.0.0.1 --port 8765 --topology config/topology.json
```

**Terminal 2 — Alice:**

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role alice --node-id alice --topology config/topology.json --coordinator http://127.0.0.1:8765
```

**Terminal 3 — Relay 1:**

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role relay --node-id r1 --topology config/topology.json --coordinator http://127.0.0.1:8765
```

**Terminal 4 — Relay 2:**

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role relay --node-id r2 --topology config/topology.json --coordinator http://127.0.0.1:8765
```

**Terminal 5 — Bob:**

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role bob --node-id bob --topology config/topology.json --coordinator http://127.0.0.1:8765
```

Open `http://127.0.0.1:8765`. Stop each process with Ctrl+C. All services bind only to `127.0.0.1`. The coordinator provisions keys independently on every link that is up; use **Provision SeQUeNCe keys** and send a message. The dashboard shows each link's distance, QBER, matching pool count, state, and failure/restore controls. It reports the selected path with each successful message. A path is eligible only when every hop is up and both endpoint pools have a key; among eligible paths the shortest total distance wins.

### Topology configuration

`config/topology.json` contains a `nodes` object with each node's role and localhost HTTP/TCP ports, plus a `links` array. Each link needs a unique `id`, endpoint node IDs `a` and `b`, `distance_km`, `noise`, and `eve` (intercept fraction); `up` sets its initial state. Coordinator provisions a separate SeQUeNCe run and matching key IDs/material for each configured link. Current path selection supports the two configured alternatives Alice–R1–Bob and Alice–R2–Bob.

## Dashboard walkthrough

Use **Provision SeQUeNCe keys** to run a separate experiment for each available link. The four rows show link state, distance, QBER, and the usable pool count on both sides. Fail either edge of the shorter route; the next send should use the other relay. Fail all four edges to see the unavailable-path response. Empty one path's link pool on both ends to demonstrate pool-based failover. Send a short message and inspect **Last message** for the route. Bob's inbox shows the decrypted message from Bob's own node.

## Tests

Run from this directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use deterministic SeQUeNCe stubs for fast orchestration checks. Relay tests cover per-link key sync, end-to-end key equality, tampered relay frames, route selection/failover, both paths down, and relay failure mid-transport. One SeQUeNCe relay end-to-end test is marked slow and runs only when `QKD_RUN_SLOW_TESTS=1`.

## Scope and remaining work

The system uses separate localhost processes, per-neighbor KMEs, four independent link pools, hop-by-hop key transport, and failover between two trusted-relay paths. The relays are trusted and see the end-to-end key. There is no privacy amplification, finite-key analysis, persistent KME state, or experiment sweep.

## Larger live topology (6 nodes)

`config/topology_6node.json` is an optional six-node, eight-link network with Alice, Bob, and four relays. It provides six candidate Alice-to-Bob routes, including routes that pass through multiple relays in sequence. The existing `config/topology.json` remains the default four-node configuration.

### Start with the PowerShell launcher

From the project root, pass a shared provisioning token. The launcher opens the coordinator first, then one PowerShell window for each node in the selected topology:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$token = 'replace-with-a-long-random-local-token'
.\scripts\start_network.ps1 -Topology config\topology_6node.json -Token $token
```

Open the printed dashboard URL, `http://127.0.0.1:8765`. To stop only this project's coordinator and node processes:

```powershell
.\scripts\stop_network.ps1
```

### Start manually in separate PowerShell windows

Use the same project directory and token in every window. Start the coordinator first.

**Window 1 — coordinator:**

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.coordinator --host 127.0.0.1 --port 8765 --topology config\topology_6node.json
```

**Windows 2–7 — Alice, four relays, and Bob:**

```powershell
# Alice
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role alice --node-id alice --topology config\topology_6node.json --coordinator http://127.0.0.1:8765
```

```powershell
# Relay 1
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role relay --node-id r1 --topology config\topology_6node.json --coordinator http://127.0.0.1:8765
```

```powershell
# Relay 2
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role relay --node-id r2 --topology config\topology_6node.json --coordinator http://127.0.0.1:8765
```

```powershell
# Relay 3
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role relay --node-id r3 --topology config\topology_6node.json --coordinator http://127.0.0.1:8765
```

```powershell
# Relay 4
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role relay --node-id r4 --topology config\topology_6node.json --coordinator http://127.0.0.1:8765
```

```powershell
# Bob
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe -m qkd_demo.node --role bob --node-id bob --topology config\topology_6node.json --coordinator http://127.0.0.1:8765
```

Each node uses its `http_port` for the KME API and its `app_port` for TCP messaging from the topology file. `--app-port` explicitly overrides the configured application port. With no topology file, the older default remains the HTTP port plus two.

Creating keys takes longer than on the four-node default because the coordinator runs a separate SeQUeNCe simulation for each of the eight links. The network map is laid out from the selected topology, including relay layers and links. The `experiments/moving_eve.py` and `experiments/benchmark_network.py` scripts support only the four-link default in `config/topology.json`; they stop with a clear message if run against this larger topology.

# Relay: SeQUeNCe QKD Key Management and Secure Messaging

Relay is a Windows-friendly course prototype that connects a SeQUeNCe BB84 + Cascade link simulation to a simple key-management service and an Alice-to-Bob encrypted messaging demonstration.

The flow is end to end:

1. SeQUeNCe simulates a BB84 link over a configurable quantum channel.
2. A custom QuantumChannel subclass can intercept and resend a selected fraction of photons.
3. Paired Cascade protocols reconcile Alice's and Bob's keys.
4. Matching simulator-produced keys enter a finite in-memory pool.
5. Alice sends a message using one key; the local service encrypts, decrypts, verifies, and consumes that key.

The browser frontend is a locally served operations console. It displays experiment conditions and measurements, pool inventory, message delivery, and link-failure controls.

> **Scope and security:** This is a software simulation and teaching prototype. It does not send real photons or connect to QKD hardware. It uses matching SeQUeNCe Cascade key output to demonstrate key lifecycle and AES-256-GCM messaging. The QBER cutoff is a simplified teaching rule; this project does not provide a complete finite-key security proof or privacy-amplification analysis. Do not use it or its keys to protect real information.

## Project structure

~~~text
qkd-secure-messaging/
├── app.py                    # application entry point
├── requirements.txt          # SeQUeNCe and AES-GCM dependencies
├── qkd_demo/
│   ├── __init__.py
│   ├── engine.py              # key pool, service, and message flow
│   ├── sequence_link.py       # SeQUeNCe BB84 + Cascade experiment
│   └── server.py              # local HTTP server and JSON API
├── web/
│   ├── index.html             # frontend structure and labels
│   ├── console.css            # responsive visual design
│   └── app.js                 # browser behavior and API requests
└── tests/
    ├── test_engine.py         # SeQUeNCe checks and service tests
    └── test_http.py           # frontend and API integration tests
~~~

## Architecture

~~~text
┌──────────────────────┐       HTTP / JSON       ┌──────────────────────────┐
│ Browser operations UI│ ◀─────────────────────▶ │ Python local HTTP server │
│ web/                 │                          │ qkd_demo/server.py       │
└──────────────────────┘                          └────────────┬─────────────┘
                                                               │
                                            ┌──────────────────▼─────────────┐
                                            │ QKD service / key manager      │
                                            │ finite pool, single-use policy │
                                            └────────┬────────────────┬──────┘
                                                     │                │
                                      generate keys  │                │ send message
                                                     ▼                ▼
                                     ┌────────────────────────┐  ┌──────────────────┐
                                     │ SeQUeNCe BB84 + Cascade│  │ AES-256-GCM      │
                                     │ quantum-link simulation│  │ Alice → Bob demo │
                                     └────────────────────────┘  └──────────────────┘
~~~

### Modules

| Module | Responsibility |
|---|---|
| qkd_demo/sequence_link.py | Creates the SeQUeNCe timeline, channels, Alice and Bob nodes, BB84 and Cascade protocols, and optional intercept-resend Eve channel. |
| qkd_demo/engine.py | Runs an experiment, applies the demo acceptance rule, stores matching key material, and handles single-use message encryption. |
| qkd_demo/server.py | Serves frontend files and the local API; binds to 127.0.0.1 by default. |
| web/index.html | Defines the experiment, key-vault, messaging, and activity sections. |
| web/console.css | Custom responsive styling without a UI framework or remote visual assets. |
| web/app.js | Connects controls to the API and updates the interface. |

There is no database. Settings, keys, event history, and counters remain in process memory and are cleared when the server stops or the session is reset.

## SeQUeNCe experiment

The run function in qkd_demo/sequence_link.py builds a discrete-event SeQUeNCe timeline. Alice and Bob are SeQUeNCe QKD nodes. It configures a light source, detectors, quantum channels, classical channels, and paired BB84 and Cascade protocols.

The Eve option subclasses SeQUeNCe's QuantumChannel. Eve intercepts a configurable fraction of non-null photons, measures each intercepted photon in a random BB84 basis, and passes the resulting state onward. The measurement can disturb later results and increase QBER.

One run requests a 256-bit Cascade key generation frame. The service records:

- Mean BB84 QBER and the number of BB84 error-rate samples.
- How many Cascade keys SeQUeNCe generated and how many matched at Alice and Bob.
- The number of photons Eve intercepted.
- Cascade throughput and SeQUeNCe's privacy-throughput estimate.
- Experiment settings and the seed.

Only matching Cascade key values from the two endpoints are eligible for the messaging demonstration. Each is stored as 32 bytes. A run can produce more keys than the pool can hold; the application imports up to the remaining pool capacity, which is 24 keys.

### Link controls and assumptions

- **Fibre distance:** 1–50 km, passed to SeQUeNCe as channel distance.
- **Channel noise:** the UI control ranges from 0% to 15% and maps to polarization fidelity using clamp(0.98 - noise, 0.5, 0.999). This is a model control, not calibrated hardware noise.
- **Eve intercept rate:** 0–100% of non-null photons on the Alice-to-Bob quantum channel.
- **QBER rule:** mean measured BB84 QBER must be below 11% to import keys. This is a teaching cutoff, not a security theorem.
- **Key acceptance:** at least one matching Cascade key must exist and QBER must be below the cutoff.
- **Reproducibility:** each run records a seed. Tests use fixed seeds; interactive runs use an unpredictable seed.

Cascade reconciles errors in this model. The UI's Cascade rate is the simulator's privacy-throughput estimate; it is not proof that the app has a composably secure final key. The project does not add a separate finite-key privacy-amplification protocol.

## Key management and message delivery

The in-memory pool stores simulator key bytes and key IDs. The browser sees the IDs and pool count, never the key bytes.

When Alice sends a message:

1. The service checks the simulated link and key availability.
2. It removes one key from the pool. Each key can be used once.
3. AES-256-GCM encrypts the message with a fresh 96-bit nonce. The key ID is authenticated as associated data.
4. Bob's local endpoint decrypts and verifies the ciphertext.
5. The interface displays Bob's message, the consumed key ID, cipher, and ciphertext.

If the pool is empty or the link is down, sending is blocked and an event is recorded. A link outage blocks message delivery in this demonstration even if some keys remain in the pool; it does not erase those keys.

AES-GCM is authenticated symmetric encryption, not a one-time pad. The app demonstrates how a messaging service can consume QKD-simulated material; it does not prove the resulting communication secure.

## Frontend

The frontend uses plain HTML, CSS, and JavaScript:

- **Experiment:** SeQUeNCe link drawing, distance/noise/Eve controls, QBER, Cascade key count, accept/reject decision, and estimated Cascade rate.
- **Key vault:** current pool, capacity, key size, consumption rule, and key IDs.
- **Message transfer:** Alice's message input and Bob's authenticated decryption receipt.
- **Activity:** link-failure and restore controls, reset, and timestamped event log.

The UI labels the link as simulated and exposes model notes. It has no UI framework, remote images, external fonts, database, or cloud dependency.

## Windows setup

Use 64-bit Python 3.12. Open PowerShell in the qkd-secure-messaging directory.

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

If the environment already exists, skip the first command. Start the prototype:

~~~powershell
.\.venv\Scripts\python.exe app.py
~~~

Open http://127.0.0.1:8765 in a browser. Stop the service with Ctrl+C in PowerShell. It binds to the local computer by default. If that port is already in use:

~~~powershell
.\.venv\Scripts\python.exe app.py --port 8770
~~~

Then open http://127.0.0.1:8770.

## Demonstration walkthrough

### Clean link and message

1. Keep the default 10 km, 1% noise, and 0% Eve settings.
2. Choose **Run SeQUeNCe experiment**. The app runs BB84 and Cascade and reports QBER and matching Cascade keys.
3. Show the key vault populate.
4. Write a short message as Alice and select **Encrypt & send**.
5. Show the authenticated receipt and the key count dropping by one.

### Eve and high QBER

1. Set Eve interception to 100% and apply settings.
2. Run SeQUeNCe again.
3. Observe increased QBER and key rejection when the measured value crosses the teaching cutoff.
4. Explain that this shows disturbance in this simulation; it is not a proof that every attack is detected.

### Link failure and key depletion

1. Select **Simulate link failure**. New generation and message actions stop.
2. Restore the link and run another experiment.
3. Send messages until the pool is empty; the next message is refused until new keys are generated.

## API

| Method and route | Purpose |
|---|---|
| GET /api/state | Reads settings, last experiment, key IDs/count, counters, and recent events. |
| POST /api/settings | Sets distance_km, channel_noise, and eve_fraction. |
| POST /api/keys/generate | Runs one SeQUeNCe experiment and imports matching keys up to count and pool capacity. |
| POST /api/messages | Encrypts and locally delivers a message using one available key. |
| POST /api/link/fail | Marks the simulated link unavailable. |
| POST /api/link/restore | Restores the link. |
| POST /api/reset | Clears settings, pool, events, counters, and last-run state. |

Example request bodies:

~~~json
{"distance_km": 10, "channel_noise": 0.01, "eve_fraction": 0.25}
{"count": 24}
{"text": "Hello, Bob."}
~~~

## Tests and initial smoke results

Install dependencies, then run from this folder:

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

Tests include real SeQUeNCe checks for a clean BB84 + Cascade run and full intercept-resend, plus service tests for input validation, key consumption, empty pools, link failure, and reset. HTTP tests cover the API and served frontend assets. They use a small deterministic experiment stub for the server's generation endpoint so API tests finish quickly.

**Observed during development on 23 October 2026:** one clean 10 km run completed with 40 Cascade keys and about 1.54% mean QBER. One 100% intercept-resend run measured about 25.62% mean QBER and produced no Cascade keys. These are single-run smoke results, not statistically robust findings or guarantees. Interactive reruns can produce different results.

## Mapping to the course report and presentation

| Course section / slide | Suggested evidence |
|---|---|
| System architecture / Slide 4 | Architecture diagram and module table above. |
| Detailed design / Slide 6 | SeQUeNCe link setup, pool lifecycle, local API, and message flow. |
| Methodology / Slide 5 | BB84 channel, Eve experiment, QBER measurement, Cascade reconciliation, and acceptance rule. |
| Database / UML / DFD | No database. Use the architecture diagram as a DFD and module table as component design. |
| Module development | Module table and API routes. |
| Working prototype / Slide 7 | Follow the live walkthrough. |
| Implementation progress | Mark only work actually run in your environment; state limitations. |
| Test cases / Slide 8 | Automated suite plus manual Eve, outage, and empty-pool demonstrations. |
| Initial results / Slides 8–9 | Repeat runs with recorded settings and seeds; don't generalize from the smoke checks. |
| Innovation and application / Slide 10 | Present this as an engineering integration, not a new BB84 protocol or a novel routing algorithm. |
| Team contribution | Fill in real work by each member. |

### Team contribution record

| Member | Actual contribution | Evidence |
|---|---|---|
| Name | Fill in work personally completed | Files, commits, tests, or presentation work |

## Limitations and future work

- Add a justified privacy-amplification and finite-key analysis before making stronger security statements.
- Run repeated trials by distance, noise, and Eve setting; retain each seed and configuration.
- Add a second QKD link and a trusted relay with separate link key pools.
- Add a documented key-delivery API if interoperability is part of the course scope.
- Connect hardware only if the course has access to an actual QKD device and interface.
- Persist experiment summaries if required, but do not store raw key material without a carefully designed key-management policy.


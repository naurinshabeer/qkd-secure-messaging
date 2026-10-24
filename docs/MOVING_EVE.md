# Moving Eve experiment

This standalone experiment compares the live relay network's QKD key generation with two in-memory classical key-exchange baselines. It controls the running coordinator through its public HTTP API; it does not modify the nodes, coordinator, dashboard, protocol, or topology file.

## Run

Start the coordinator, Alice, Relay 1, Relay 2, and Bob in five PowerShell terminals using the commands in `README.md`; all five processes must use the same `QKD_PROVISION_TOKEN`. Then, from another terminal:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe experiments\moving_eve.py --out experiments\results\moving-eve
```

The script defaults to seven rounds, five messages per round, and a 75% intercept fraction for links assigned Eve. Use `--quick` for three rounds and two messages per round; the five-point intercept sweep still runs. Other options are `--rounds N`, `--messages N`, `--eve-fraction X`, `--base-url http://127.0.0.1:8765`, and `--out PATH`. `matplotlib` from `requirements-experiments.txt` is needed to write the PNG. Key generation may take several minutes; the client waits for the synchronous coordinator request to finish and reports request timeouts separately from connection failures. If the coordinator is down, the script exits with the expected URL and startup instruction. API errors include a reminder to check that all processes use the same token and topology ports.

A full run invokes SeQUeNCe up to 48 times (four links for each of seven scenarios and five intercept fractions). It was not timed here; using the 14.38-second average per run recorded by the existing pilot sweep in `docs/EXPERIMENTS.md`, the estimate is about 11 minutes 30 seconds plus HTTP and message handling. Actual runtime depends on the machine and simulator runs.

**This experiment resets node pools and resets link availability at the start of every scenario round.** It also updates settings, provisions keys, and sends messages on the live network. Run it when that network state can be reset.

Outputs:

- `moving_eve.csv`: one row for each scenario round and each additional intercept-fraction run. Round rows include configured Eve links, per-link QBER, whether accepted pool keys exist, per-link remaining pool counts, paths, delivery/block counts, and exposure counts for each method. The final five rows contain QBER and accepted/pool values for Eve fractions 0.1, 0.25, 0.5, 0.75, and 1.0 on A-R1.
- `moving_eve_timeline.png`: per-link QBER with the 11% cutoff, QKD path labels, and delivered/blocked/exposed counts by scenario round.

## Scenario and comparison methods

The default Eve schedule is: no Eve; Eve on A-R1; Eve moves to R2-B; Eve on A-R1 and R2-B (one hop on each candidate route); Eve on R1-B; Eve moves to A-R2; Eve leaves. At each round the script resets the network, applies Eve to those links while setting all other links to zero interception, generates keys, sends the configured number of messages, and records the resulting state.

1. **QKD relay network:** uses the actual coordinator path selection, SeQUeNCe link results, key pools, and end-to-end sends. A message counts as exposed only when its successful route crosses a link where Eve is configured and that link has accepted keys in its pool. This is deliberately conservative accounting based on model state; it does not estimate how much information Eve learned.
2. **Classical X25519 without authentication:** the script performs actual X25519 exchanges for the network hops. On an Eve-controlled hop, Eve creates two exchanges and relays between different endpoint keys. The baseline assumes this active man-in-the-middle is not detected, so a route is available and messages crossing the attacked hop are counted as exposed.
3. **Classical X25519 with Ed25519 signatures:** the script performs X25519 exchanges and signs the exchange transcript with Ed25519. Verification uses the peer signing public key as a pinned identity. A substituted ephemeral key fails verification, is detected, and blocks that hop; route selection uses another Eve-free route if available, or reports the messages blocked if no route remains.

The classical cases are in-memory comparisons, not connections to the running nodes. Classical traffic delivery counts are scenario-model counts, not measured socket delivery. Each hop handshake is performed once per round and treated as reusable for that round's message count. The Ed25519 identity keys are generated for the baseline process and treated as pre-pinned; the experiment does not implement a certificate authority or key enrollment. Both classical baselines use the configured network routes and link distances to choose the shortest available path.

The QKD key pools are volatile. Rejected links have no pool keys after reset and are not considered an exposure path. When QBER remains below the 11% acceptance cutoff, keys are accepted even though Eve may hold partial information. This system has no privacy amplification, so the accepted keys are not made safe against that partial information. The extra intercept-fraction rows expose this behavior directly; do not interpret an accepted key as proof that Eve learned nothing.

## Results

No live-network run was performed while preparing this document, so no numeric observations are claimed here. Run the command above to produce measurements for your local SeQUeNCe configuration; the CSV and PNG are generated from that run.

## What this shows and what it does not show

An authenticated classical key exchange can also stop a man-in-the-middle when the peer identity key is already trusted. QKD still needs an authenticated classical channel to prevent an attacker from impersonating the endpoints during basis reconciliation and related communication. The project models Eve on the quantum link and the authenticated provisioning channel separately; this moving-Eve script does not attack the provisioning-token channel.

The relays are trusted and see the end-to-end key during hop-by-hop transport. Eve can still cause denial of service by raising errors until keys are rejected or by making routes unavailable. The quantum link and its QBER are generated by SeQUeNCe software simulation; this is not photon hardware, a finite-key security proof, a privacy-amplified protocol, or a real-world performance/security result. The classical baselines are illustrative software exchanges and route-count models, not a security evaluation.

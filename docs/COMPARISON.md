# Key-establishment comparison

This experiment compares locally measured classical cryptographic operations with existing SeQUeNCe BB84 pilot data. It intentionally does not compare classical wall-clock operation time with SeQUeNCe simulator wall-clock time: the simulator's compute time is not quantum-link time.

## Run

From the repository root in PowerShell:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
.\.venv\Scripts\python.exe experiments\compare_key_establishment.py --repetitions 1000 --results experiments\results --out experiments\results
```

For a quick smoke run (five classical repetitions):

```powershell
.\.venv\Scripts\python.exe experiments\compare_key_establishment.py --quick --out experiments\results
```

The script reads the existing `experiments\results\summary.csv` and writes `compare_key_establishment.csv` and `compare_key_establishment.png` in the output folder. The PNG is produced with the optional `matplotlib` dependency from `requirements-experiments.txt`. ML-KEM is checked at runtime; no dependency is installed. The installed `cryptography` library did not expose a usable ML-KEM API in the run recorded here, so it is listed as **not measured here**.

## Measurement definitions

“Bytes on the wire” means the raw public-key and ciphertext bytes only. It excludes TLS, certificates or identity-key distribution unless separately called out, message headers, transport framing, retransmissions, and other protocol overhead. X25519 counts two raw 32-byte ephemeral public keys per handshake. RSA reports its DER SubjectPublicKeyInfo public-key size separately; the per-operation ciphertext count is the RSA-OAEP ciphertext. RSA key generation is timed separately from encryption and decryption. PSK+HKDF exchanges zero key bytes in this measurement because both parties are assumed to already possess the same 32-byte secret; this moves the key-distribution problem elsewhere.

Classical operation timings are repeated on this computer and reported as median and nearest-rank p95. The default is 1,000 repetitions for each operation; RSA key generation is capped at 100 repetitions and its actual count is in the CSV. These values describe this runtime and hardware only. They are not compared numerically with BB84 simulator runtime.

The BB84 rows are derived from the existing `summary.csv`, not rerun during this comparison. They include the recorded number of runs per point, mean simulated QBER, acceptance rate, and signals per accepted final key bit. The checked-in summary currently has **2 runs per point**; this small pilot does not support a statistical confidence claim. Distance results and Eve-sweep results are **simulated**. The simulator models BB84 behavior and does not use photon hardware.

## Results recorded in the current output

Classical operation results (per operation unless noted):

| Method | Repetitions | Median (ms) | p95 (ms) | Bytes on wire | Public key (bytes) | Ciphertext (bytes) |
|---|---:|---:|---:|---:|---:|---:|
| X25519 + HKDF, both sides | 1,000 | 0.137 | 0.145 | 64 | 32 per side | — |
| RSA-2048 key generation | 100 | 47.075 | 192.438 | 0 | — | — |
| RSA-2048 OAEP encrypt | 1,000 | 0.041 | 0.047 | 256 | 294 (DER SPKI) | 256 |
| RSA-2048 OAEP decrypt | 1,000 | 0.887 | 1.140 | 256 | 294 (DER SPKI) | 256 |
| Pre-shared key + HKDF, both sides | 1,000 | 0.015 | 0.015 | 0 | — | — |
| ML-KEM | Not measured here | — | — | — | — | — |

Provenance for these measured results: **platform** Windows-11-10.0.26200-SP0; **Python** 3.12.4; **cryptography** 47.0.0; **CPU count** 20; **repetitions** 1,000 for operations and 100 for RSA key generation; **UTC timestamp** 2026-10-24T13:40:35Z. The CSV repeats these metadata fields on every row.

BB84 distance pilot (simulated; **2 runs per point**):

| Distance (km) | Signals / accepted key bit | Mean QBER | Acceptance rate |
|---:|---:|---:|---:|
| 1 | 11.91 | 1.32% | 100% |
| 5 | 13.89 | 1.47% | 100% |
| 10 | 13.88 | 1.49% | 100% |
| 20 | 18.31 | 1.49% | 100% |
| 30 | 25.74 | 1.49% | 100% |
| 40 | 36.58 | 1.51% | 100% |
| 50 | 53.44 | 1.38% | 100% |

BB84 Eve-intercept pilot (simulated; **2 runs per point**):

| Eve fraction | Mean QBER | Acceptance rate |
|---:|---:|---:|
| 0 | 1.46% | 100% |
| 0.1 | 3.81% | 100% |
| 0.25 | 7.23% | 100% |
| 0.5 | 13.45% | 0% |
| 0.75 | 19.76% | 0% |
| 1.0 | 25.79% | 0% |

At intercept fractions 0.1 and 0.25, keys were accepted even though Eve may hold partial information. This project has no privacy amplification, so acceptance does not remove that partial information. Rejection occurs at the higher fractions in this two-run pilot; this is not a guarantee that every attack is detected.

## Active attacker demonstration

The CSV includes two small in-memory man-in-the-middle demonstrations. In the unauthenticated X25519 case, Eve substitutes ephemeral keys and establishes separate HKDF keys with Alice and Bob. The output records `alice_key == bob_key` as false, `alice_key == eve_alice_key` as true, and `bob_key == eve_bob_key` as true. The protocol has no step that compares Alice's key with Bob's, so this run records no detection; detection would need an out-of-band or authenticated check.

In the signed case, Alice and Bob each sign their ephemeral X25519 public key with Ed25519. Each verifier uses an identity public key that it already trusts. When Eve substitutes her own ephemeral key, signature verification fails and the handshake aborts. Signatures work here because the verifier already trusts the signer's public key: this moves the trust problem to the trust anchor rather than eliminating it. These are in-memory demonstrations, not a networked or adversarial security evaluation.

The BB84 comparison is the existing Eve sweep above: at low intercept fractions (0.1 and 0.25), the two-run summaries show accepted keys despite possible partial Eve information; at 0.5 and above those pilot runs were rejected. QKD itself requires an authenticated classical channel to prevent endpoint impersonation during the classical discussion; this is a protocol requirement, not something established by this simulator, and should be verified against a cited source in a formal report. The sweep is a software simulation, not a hardware attack test.

## Qualitative comparison matrix

Each cell identifies its evidence type. “Qualitative” entries are explanatory scope statements, not results measured by this benchmark. Any broader claim about security, threat models, deployment maturity, or economics is explicitly marked for source verification rather than presented as established fact here.

| Topic | BB84 project model | X25519 + HKDF | RSA-2048 OAEP | PSK + HKDF | ML-KEM |
|---|---|---|---|---|---|
| Security basis | **Simulated:** BB84-style signal/basis model and QBER threshold; no finite-key proof. | **Qualitative:** classical elliptic-curve key agreement; external security claim to be verified against a cited source. | **Qualitative:** public-key encryption of a random key; external security claim to be verified against a cited source. | **Qualitative:** secrecy depends on already sharing the PSK. | **Qualitative:** post-quantum KEM family; external security and standardization claims to be verified against a cited source. |
| Quantum-computer threat | **Qualitative:** intended to use a different security basis; this project does not prove security. | **Qualitative:** threat assessment not measured; external claim to be verified against a cited source. | **Qualitative:** threat assessment not measured; external claim to be verified against a cited source. | **Qualitative:** depends on PSK strength and management; threat claim not evaluated here. | **Qualitative:** intended for post-quantum use; external claim to be verified against a cited source. |
| Harvest now, decrypt later | **Qualitative:** this demo does not evaluate recorded traffic or key lifetime. | **Qualitative:** not measured here; external claim to be verified against a cited source. | **Qualitative:** not measured here; external claim to be verified against a cited source. | **Qualitative:** not measured here; outcome depends on PSK handling and system design. | **Qualitative:** not measured here; external claim to be verified against a cited source. |
| Infrastructure and distance | **Simulated:** four configured localhost links; per-distance behavior is software-generated. | **Measured here:** two 32-byte public keys per handshake; no network deployment benchmark. | **Measured here:** public-key and ciphertext byte sizes; public-key distribution cost is separate. | **Measured here:** zero key bytes exchanged; prior distribution is assumed. | **Qualitative:** not measured here because the installed library lacks a usable API. |
| Trusted relays | **Simulated:** relays are trusted and see the end-to-end key. | **Qualitative:** end-to-end key establishment does not require this project's relay key transport; network design not evaluated. | **Qualitative:** endpoint key transport; deployment architecture not evaluated. | **Qualitative:** distribution path is external to this measurement and must be trusted. | **Qualitative:** deployment architecture not evaluated. |
| Authentication needed | **Qualitative:** the classical discussion channel must be authenticated; protocol claim to be verified against a cited source. Project provisioning authentication is modeled separately. | **Qualitative:** an identity-authentication method is needed to resist active substitution; external claim to be verified against a cited source. | **Qualitative:** public-key identity validation is outside the OAEP operation measured here. | **Qualitative:** PSK possession can authenticate parties only within a system design; not tested here. | **Qualitative:** identity authentication is outside the KEM operation; external claim to be verified against a cited source. |
| Maturity | **Simulated:** course prototype, not production QKD. | **Qualitative:** not assessed in this experiment; external claim to be verified against a cited source. | **Qualitative:** not assessed in this experiment; external claim to be verified against a cited source. | **Qualitative:** not assessed in this experiment. | **Qualitative:** not measured here; external standardization/maturity claim to be verified against a cited source. |
| Cost | **Qualitative:** no hardware or operating cost measured. | **Qualitative:** no deployment or lifecycle cost measured. | **Qualitative:** no deployment or lifecycle cost measured. | **Qualitative:** no provisioning or management cost measured. | **Qualitative:** no deployment or lifecycle cost measured. |

## Where QKD makes sense, where it does not

This prototype is useful for studying how a QBER cutoff, link distance, interception, key pools, and trusted relay paths affect a modeled QKD network. It demonstrates one trade-off in the checked-in simulation: signals needed per accepted bit rise with modeled distance, while the two-run distance pilot's QBER remains in a narrow range. This is a software-model observation, not a hardware performance estimate.

QKD is not established by this project as a replacement for classical cryptography. The prototype has simulated quantum links, trusted relays, no privacy amplification, no finite-key analysis, and no photon hardware. A practical choice depends on deployment requirements, authenticated classical communication, network architecture, implementation assurance, and costs; those factors were not measured here. This comparison declares no overall winner.

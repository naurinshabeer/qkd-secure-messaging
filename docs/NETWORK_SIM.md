# Standalone trusted-relay network simulator

This is a separate, discrete-time model for studying larger trusted-relay networks. It does not start or change the project's live node processes. The quantum-link inputs come from the checked-in BB84 pilot in `experiments/results/summary.csv`.

## Run

From the project root in Windows PowerShell:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
.\.venv\Scripts\python.exe -m experiments.network_sim.run_experiments --seeds 5 --pulse-rate 1000000 --out experiments\results\network_sim
```

The smoke run uses one seed and short sweeps:

```powershell
.\.venv\Scripts\python.exe -m experiments.network_sim.run_experiments --quick --out experiments\results\network_sim-quick
```

The normal run writes `network_sim_results.csv` and `network_sim_results.png`. Each CSV row repeats `assumed_pulse_rate`; the command prints it and the chart includes it in the title. Summary rows provide the mean and approximate 95% confidence interval across seeds, while the individual seed rows remain available for inspection. Run the simulator's focused tests separately:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s experiments\network_sim -p test_network_sim.py -v
```

The complete repository suite is run with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Topologies and shared traffic

All graphs are built in code. Their random geometry uses fixed seed `20261024`, so graph structure is repeatable.

| Graph | Nodes | Links | Request pairs |
|---|---:|---:|---:|
| Four-node diamond | 4 | 4 | Alice–Bob |
| Ring | 12 | 12 | 4 |
| Grid | 16 (4×4) | 24 | 4 |
| Metro mesh | 15 | 21 | 4 |

The diamond uses the project's existing edge lengths (8, 7, 10, and 9 km) and validates the routing. Ring and grid distances use their generated coordinates. Metro nodes are placed at random in a 38×38 km square; edges up to 11 km are added, then shortest inter-component edges are added until connected. In the ring, grid, and mesh, four user pairs share transit nodes and links. Pair definitions and each edge's distance are recorded in the CSV.

## Link model and explicit assumptions

The CSV currently contains two BB84 simulator runs per pilot point. The link model linearly interpolates mean **signals per accepted final key bit** and mean **QBER by distance** from those distance-sweep points. It also reads the Eve-fraction QBER curve. Interpolation is limited to the observed 1–50 km interval; links outside it have zero key-generation rate. The normal link QBER is the distance-interpolated value. When the simulated attacker occupies a link, the model uses the larger of that distance QBER and the QBER at full interception from the Eve sweep. A link at or above the 11% cutoff has zero new key-generation rate.

**ASSUMPTION — pulse rate:** `--pulse-rate` is an assumed signal pulse rate per link, default `1,000,000 signals/second/link`. The bit-generation rate is calculated as:

```text
rate_bits_per_s = assumed_pulse_rate / signals_per_accepted_key_bit
```

This pulse rate is a tunable modeling input, not a measured property of hardware. Absolute capacity, queueing, and service values depend on it; read the trends and policy comparisons more heavily than absolute values.

Each link has a shared, finite pool with capacity 32 × 256 bits (32 end-to-end-key requests). Normal experiments start with a full pool; the moving-attacker experiment starts with 8 keys per link. A delivered end-to-end key is 256 bits and consumes 256 bits from every link on its route. At each discrete time step, available healthy links refill their pools at the derived bit rate, capped at capacity, before arrivals are routed. Link failures stop both refills and use of that edge. When Eve is on a link, new key generation stops, but previously accepted pool material remains available until consumed.

Arrivals are independent Poisson counts per pair per step. The configured load is the aggregate request rate shared evenly among the four pairs. One simulation step is one second. Every load point, failure case, trust case, and attacker case uses the same per-seed arrival stream for the routing-policy comparison. Policy definitions:

- **Fewest hops:** Dijkstra with unit edge weights.
- **Shortest distance:** Dijkstra weighted by link distance in kilometres.
- **Key aware:** excludes links with less than one 256-bit key, then favors links with fuller pools using a fill-level penalty.

The failure sweep randomly removes 0–5 metro links per seed. Trust exposure counts successful requests whose path includes a node as an intermediate relay; it excludes the sender and recipient for that request. Under the trusted-relay model, that relay sees the end-to-end key, so a compromised relay can read those routed messages.

The moving Eve experiment lasts six epochs of ten time steps each. The attacker follows a seeded walk between links that share a node. Widths 1, 2, and 3 mean that many links are attacked at once; all policies and traffic seeds share the same attack schedule for a given width. Full interception sets the attacked link QBER using the measured Eve=1 pilot point, above the cutoff, so no new accepted keys are generated there. The CSV separately records requests routed over an attacked link and `requests_exposed_to_eve`. The latter is zero in this model: the attacked link's new keys are rejected, and pre-existing accepted keys are not assumed to be known to Eve. This is a model accounting rule, not a claim that all real attacks expose zero information. Recovery time is the time after a link leaves the attack until its pool again has at least one 256-bit key; retained keys can make this zero.

Confidence intervals are approximate normal 95% intervals (`mean ± 1.96 × sample standard deviation / sqrt(seed count)`) over independent traffic seeds. Five seeds are a small sample, so intervals are descriptive, not strong statistical evidence. Link model inputs themselves come from only two simulator runs per point; their uncertainty is not propagated into network results.

## Outputs

`network_sim_results.csv` is long-form. It includes the topology catalog, each link's distance/QBER/signals-per-bit/rate, individual seed measurements, and aggregate means/intervals for:

- validation on the four-node diamond;
- served fraction against aggregate request load for all three policies;
- pair connectivity and served fraction after random link removals;
- share of delivered requests that would expose each intermediate relay if compromised;
- served fraction by time step for moving-Eve widths 1, 2, and 3, attack-link QBER, requests crossing attacked links, requests exposed under the model definition, and pool recovery time.

`network_sim_results.png` contains the load, failure, trust-exposure, and three moving-Eve plots. The moving panels mark epoch changes and identify the attacker's link position.

## Observed trends in the generated five-seed run

The checked-in CSV/PNG were generated with `--seeds 5` and a pulse-rate assumption of 1,000,000 signals/second/link.

- **Load:** All three policies served essentially all requests through 25 requests/second. At 100 requests/second they served about 73.5–73.7%; at 200 requests/second, fewest-hops and shortest-distance served about 40.6%, while key-aware served about 49.4%.
- **Failures:** The mesh kept all four pairs connected with no failed links, and about 55% connected after removing four or five random links. Served fraction broadly followed pair connectivity in this run; five random failures retained about 55–56% service depending on the sampled failures.
- **Relay exposure:** The most-used intermediate relay was `m00`, included in about 49.9% of delivered requests for each policy. This measures route exposure in this topology and traffic sample; it is not a security ranking of relays.
- **Moving Eve:** With one attacked link, mean served fraction across time was about 98.2% for fewest-hops/shortest-distance and 100% for key-aware. With two attacked links, those means were about 84.8% and 90.2%; with three, about 65.1% and 72.5%. The largest mean recovery time was 0.4 seconds. Requests crossing attacked links were recorded, but modeled cryptographic exposure was zero because the attacked links generated no accepted new keys above the QBER cutoff.

These figures are outputs of a network model fed by a SeQUeNCe software-simulation pilot, not hardware measurements. The pilot itself has only two runs per point, and the network experiment uses five traffic seeds. The outputs illustrate behavior under these assumptions; absolute service values change with the assumed pulse rate, pool settings, traffic, and topology. Do not interpret them as real QKD capacity, hardware performance, or a security proof.

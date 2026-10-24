# Experiments

These standalone tools use the existing simulator and localhost node APIs. They do not alter the network protocol or dashboard behavior. Install the optional plotting dependency separately when plots are needed:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
.\.venv\Scripts\python.exe -m pip install -r requirements-experiments.txt
```

## Parameter sweeps

Run a small smoke sweep (one seed and two points from each parameter):

```powershell
.\.venv\Scripts\python.exe experiments\run_sweeps.py --quick
```

Run the full sweep (20 seeds at each of 20 parameter points, 400 simulator runs total):

```powershell
.\.venv\Scripts\python.exe experiments\run_sweeps.py --seeds 20 --out experiments\results
```

The script calls `run_sequence_bb84` directly. It varies distance (1, 5, 10, 20, 30, 40, 50 km), noise (0, 0.01, 0.02, 0.05, 0.08, 0.11, 0.15), or Eve intercept fraction (0, 0.1, 0.25, 0.5, 0.75, 1.0), holding the other settings at the `LinkSettings` defaults. The simulator's acceptance rule is QBER below 0.11 and at least one matching Cascade key.

The checked-in `experiments/results` dataset is a two-seed pilot across all 20 points (`--seeds 2`), not a completed default 20-seed sweep. In that pilot, the 40 SeQUeNCe runs accumulated 1,213 seconds (about 20 minutes 13 seconds) of measured simulation-call runtime. At that pilot's mean of 30.3 seconds per call, the default 400-run sweep is estimated at about 3 hours 22 minutes of simulator-call time; actual wall time will depend on the machine and run settings.

`runs.csv` contains one row per simulator run, including seed, QBER, accepted flag, final matching-key length, accepted-key yield, simulator signals, and wall-clock time. The current simulator does not expose raw or sifted key lengths, so those columns are empty rather than inferred. Final length is matching Cascade keys × 256 bits; accepted-key yield is zero for a rejected run. `summary.csv` has one row per parameter point with the mean, sample standard deviation, approximate normal 95% confidence interval for QBER, accepted-key yield and key rate, plus acceptance rate and mean runtime. The intervals use 1.96 × standard error (and are descriptive for this finite seed count).

Produce seven error-bar plots (three QBER, three accepted-key yield, and key rate versus distance):

```powershell
.\.venv\Scripts\python.exe experiments\plot_results.py --input experiments\results --out experiments\results
```

The QBER plots show the 0.11 acceptance cutoff. Accepted-key-yield and key-rate plots use the summary's 95% confidence intervals as error bars.

## Network latency benchmark

Start the coordinator, Alice, Relay 1, Relay 2, and Bob in five terminals using the run commands in the README. In a sixth terminal, set the same provisioning token and run:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe experiments\benchmark_network.py --messages 50 --out experiments\results
```

The benchmark checks the live coordinator, picks the shortest available configured path, provisions additional SeQUeNCe keys as necessary, times 50 normal-path messages, fails that route's first link, then times the first failover message and the next ten. Timing is end-to-end coordinator request latency and excludes key-generation calls. It restores the failed link when the failover portion exits. `network_latency_samples.csv` contains each latency and the actual path; `network_latency_summary.csv` reports count, mean, median, and nearest-rank p95 for normal, first failover, and subsequent failover messages; `network_latency.png` is a box plot. If the coordinator is not running, the script exits with a message identifying the expected URL.

## Observed trends

These observations are from the two-seed full-parameter pilot in this repository's `experiments/results` directory. Two seeds are too few to establish stable statistical conclusions.

- **Distance:** Mean QBER stayed between 0.0135 and 0.0151 from 1–50 km, with all 14 pilot runs accepted. It did not rise monotonically in this small sample.
- **Noise and Eve:** Mean QBER increased across the noise points from 0.0097 at 0 noise to 0.0827 at 0.15 noise; acceptance was 50% at 0.15 because acceptance also requires a matching Cascade key. Mean Eve QBER rose from 0.0149 at zero interception to 0.2548 at full interception; the pilot's acceptance rate was 100% through 0.25 Eve fraction and 0% from 0.5 through 1.0.
- **Yield and rate:** Distance runs each yielded 10,240 accepted final key bits in this pilot, while mean estimated key rate fell from about 597 bits/s at 1 km to 127 bits/s at 50 km as runtime increased. The noise and Eve yield plots also reflect the acceptance gate; rejected trials contribute zero accepted-key yield.

All results come from the project's SeQUeNCe-based simulation, not photon hardware. They illustrate the behavior of this model and are not measurements or predictions of real QKD hardware performance. The network latency benchmark has not been run for the checked-in dataset; its output files are created only when run against a live local topology. Any reported network timings are localhost prototype measurements, not production network benchmarks.

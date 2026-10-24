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

The regenerated two-seed pilot took 575.4 seconds (about 9 minutes 35 seconds) for 40 SeQUeNCe calls, averaging 14.38 seconds per call. At that rate, the default 400-run sweep is estimated at about 1 hour 36 minutes of simulator-call time; actual wall time varies by machine and settings. The full 20-seed sweep was not run for this update.

`runs.csv` contains one row per simulator run, including seed, QBER, acceptance, final matching-key length, accepted final key bits, `signals_sent`, and simulator wall-clock runtime. The simulator does not expose raw or sifted bit counts, so those columns are empty rather than inferred. Final key length is matching Cascade keys × 256 bits. Rejected runs contribute zero accepted final key bits and therefore zero key efficiency. Key efficiency is accepted final key bits divided by `signals_sent` (accepted final key bits per signal). Its inverse, signals needed per accepted key bit, is undefined and left blank on rejected runs. `summary.csv` reports the mean, sample standard deviation, and approximate normal 95% confidence interval for QBER, key efficiency, inverse efficiency (using only runs with accepted bits), and acceptance rate. Intervals use 1.96 × standard error and are descriptive for this finite seed count. Simulator runtime is recorded only as a compute-time diagnostic; it is not used as a key-rate metric.

Produce twelve error-bar plots: three QBER, three key-efficiency, three signals-needed-per-key-bit, and three acceptance-rate plots. The inverse-efficiency plot omits parameter points with no accepted keys because the inverse is undefined there.

```powershell
.\.venv\Scripts\python.exe experiments\plot_results.py --input experiments\results --out experiments\results
```

The QBER plots show the 0.11 acceptance cutoff. The plots use the summary's 95% confidence intervals as error bars; acceptance rate is shown as a percentage.

## Network latency benchmark

Start the coordinator, Alice, Relay 1, Relay 2, and Bob in five terminals using the run commands in the README. In a sixth terminal, set the same provisioning token and run:

```powershell
cd C:\Users\SI\Documents\qkd-network\qkd-secure-messaging
$env:QKD_PROVISION_TOKEN = 'replace-with-a-long-random-local-token'
.\.venv\Scripts\python.exe experiments\benchmark_network.py --messages 50 --out experiments\results
```

The benchmark checks the live coordinator, picks the shortest available configured path, provisions additional SeQUeNCe keys as necessary, times 50 normal-path messages, fails that route's first link, then times the first failover message and the next ten. Timing is end-to-end coordinator request latency and excludes key-generation calls. It restores the failed link when the failover portion exits. `network_latency_samples.csv` contains each latency and the actual path; `network_latency_summary.csv` reports count, mean, median, and nearest-rank p95 for normal, first failover, and subsequent failover messages; `network_latency.png` is a box plot. If the coordinator is not running, the script exits with a message identifying the expected URL.

## Observed trends

These observations are from the regenerated two-seed pilot in `experiments/results`. Two seeds per parameter point are too few to establish stable statistical conclusions.

- **Distance:** Mean QBER ranged from 0.0132 to 0.0151 across 1–50 km and did not rise steadily. QBER stays roughly flat with distance in this model while signals needed per key bit rises, from about 11.91 at 1 km to 53.44 at 50 km.
- **Noise:** Mean QBER increased from 0.0099 at zero noise to 0.0814 at noise 0.15. All runs remained accepted in this pilot, and measured key efficiency stayed about 0.072 accepted bits per signal across the noise points.
- **Eve:** Mean QBER increased from 0.0146 with no interception to 0.2579 at full interception. All runs were accepted through Eve fraction 0.25, while none were accepted at fractions 0.5 and above; inverse efficiency is undefined at those points.

All results come from the project's SeQUeNCe-based simulation, not photon hardware. They illustrate the behavior of this model and are not measurements or predictions of real QKD hardware performance. The network latency benchmark has not been run for the checked-in dataset; its output files are created only when run against a live local topology. Any reported network timings are localhost prototype measurements, not production network benchmarks.

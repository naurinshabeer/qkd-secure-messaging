"""Benchmark classical key establishment and summarize the existing BB84 pilot."""

from __future__ import annotations

import argparse
import csv
import importlib
import statistics
import time
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, x25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "experiments" / "results"
CSV_FIELDS = [
    "category", "method", "parameter", "parameter_value", "runs",
    "median_ms", "p95_ms", "bytes_on_wire", "public_key_bytes",
    "ciphertext_bytes", "signals_per_accepted_key_bit", "qber",
    "acceptance_rate", "note",
]
RSA_KEYGEN_REPETITIONS = 100
RSA_BITS = 2048
RSA_MESSAGE_BYTES = 32
X25519_RAW_PUBLIC_BYTES = 32


def _hkdf(shared_secret: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"key-establishment-comparison-v1").derive(shared_secret)


def _measure(repetitions: int, operation: Callable[[], Any]) -> list[float]:
    samples = []
    for _ in range(repetitions):
        start = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, int(0.95 * len(ordered) + 0.999999) - 1)]


def _timing_row(method: str, samples: list[float], *, wire: int | str,
                public_key: int | str = "", ciphertext: int | str = "",
                note: str = "") -> dict[str, Any]:
    return {
        "category": "classical_benchmark", "method": method, "parameter": "",
        "parameter_value": "", "runs": len(samples),
        "median_ms": statistics.median(samples), "p95_ms": _percentile_95(samples),
        "bytes_on_wire": wire, "public_key_bytes": public_key,
        "ciphertext_bytes": ciphertext, "signals_per_accepted_key_bit": "",
        "qber": "", "acceptance_rate": "", "note": note,
    }


def _classical_rows(repetitions: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def x25519_handshake() -> None:
        alice = x25519.X25519PrivateKey.generate()
        bob = x25519.X25519PrivateKey.generate()
        alice_key = _hkdf(alice.exchange(bob.public_key()))
        bob_key = _hkdf(bob.exchange(alice.public_key()))
        if alice_key != bob_key:
            raise AssertionError("X25519 peers derived different keys")

    rows.append(_timing_row(
        "X25519 + HKDF (both sides)", _measure(repetitions, x25519_handshake),
        wire=2 * X25519_RAW_PUBLIC_BYTES, public_key=X25519_RAW_PUBLIC_BYTES,
        note="Per handshake: two raw 32-byte ephemeral public keys; HKDF on each side.",
    ))

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_BITS)
    public_key = private_key.public_key()
    der_public = public_key.public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    ciphertext_holder: list[bytes] = []

    keygen_repetitions = min(repetitions, RSA_KEYGEN_REPETITIONS)
    rows.append(_timing_row(
        "RSA-2048 key generation",
        _measure(keygen_repetitions, lambda: rsa.generate_private_key(public_exponent=65537, key_size=RSA_BITS)),
        wire="", note="Separate setup measurement; up to 100 generated keypairs.",
    ))

    def rsa_encrypt() -> None:
        ciphertext_holder[:] = [public_key.encrypt(
            b"K" * RSA_MESSAGE_BYTES,
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None),
        )]

    def rsa_decrypt() -> None:
        plaintext = private_key.decrypt(
            ciphertext_holder[0],
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None),
        )
        if len(plaintext) != RSA_MESSAGE_BYTES:
            raise AssertionError("RSA-OAEP did not recover the 32-byte key")

    # Prime the holder outside the timed decrypt operation.
    rsa_encrypt()
    ciphertext_size = len(ciphertext_holder[0])
    rows.append(_timing_row(
        "RSA-2048 OAEP encrypt", _measure(repetitions, rsa_encrypt),
        wire=ciphertext_size, public_key=len(der_public), ciphertext=ciphertext_size,
        note="Per operation; public key distribution is a separate one-time 294-byte DER SPKI item here.",
    ))
    rows.append(_timing_row(
        "RSA-2048 OAEP decrypt", _measure(repetitions, rsa_decrypt),
        wire=ciphertext_size, public_key=len(der_public), ciphertext=ciphertext_size,
        note="Per operation; recipient already has the private key.",
    ))

    psk = b"comparison-demo-pre-shared-key-32b"[:32]

    def psk_hkdf_both_sides() -> None:
        alice_key = _hkdf(psk)
        bob_key = _hkdf(psk)
        if alice_key != bob_key:
            raise AssertionError("PSK peers derived different keys")

    rows.append(_timing_row(
        "Pre-shared key + HKDF (both sides)", _measure(repetitions, psk_hkdf_both_sides),
        wire=0, note="No key bytes exchanged; this moves the key-distribution problem elsewhere.",
    ))
    return rows


def _mlkem_rows(repetitions: int) -> list[dict[str, Any]]:
    try:
        module = importlib.import_module("cryptography.hazmat.primitives.asymmetric.ml_kem")
    except ImportError:
        return [{"category": "classical_benchmark", "method": "ML-KEM",
                 "parameter": "", "parameter_value": "", "runs": 0,
                 "median_ms": "", "p95_ms": "", "bytes_on_wire": "",
                 "public_key_bytes": "", "ciphertext_bytes": "",
                 "signals_per_accepted_key_bit": "", "qber": "",
                 "acceptance_rate": "", "note": "not measured here: installed cryptography has no ML-KEM API."}]
    kem_class = getattr(module, "MLKEM768", None)
    if kem_class is None or not callable(getattr(kem_class, "generate", None)):
        return [{"category": "classical_benchmark", "method": "ML-KEM",
                 "parameter": "", "parameter_value": "", "runs": 0,
                 "median_ms": "", "p95_ms": "", "bytes_on_wire": "",
                 "public_key_bytes": "", "ciphertext_bytes": "",
                 "signals_per_accepted_key_bit": "", "qber": "",
                 "acceptance_rate": "", "note": "not measured here: installed cryptography has no supported ML-KEM API."}]

    try:
        private = kem_class.generate()
        public = private.public_key()
        encapsulate = getattr(public, "encapsulate")
        decapsulate = getattr(private, "decapsulate")
        shared_secret, ciphertext = encapsulate()
        if decapsulate(ciphertext) != shared_secret:
            raise AssertionError("ML-KEM self-check did not derive the same secret")
        public_bytes = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except (AttributeError, TypeError, ValueError, NotImplementedError):
        return [{"category": "classical_benchmark", "method": "ML-KEM",
                 "parameter": "", "parameter_value": "", "runs": 0,
                 "median_ms": "", "p95_ms": "", "bytes_on_wire": "",
                 "public_key_bytes": "", "ciphertext_bytes": "",
                 "signals_per_accepted_key_bit": "", "qber": "",
                 "acceptance_rate": "", "note": "not measured here: installed ML-KEM API is not usable."}]

    holder: list[tuple[bytes, bytes]] = []

    def do_encapsulate() -> None:
        holder[:] = [public.encapsulate()]

    def do_decapsulate() -> None:
        secret, ciphertext_bytes = holder[0]
        if private.decapsulate(ciphertext_bytes) != secret:
            raise AssertionError("ML-KEM decapsulation mismatch")

    do_encapsulate()
    ct_size = len(holder[0][1])
    rows = [_timing_row("ML-KEM-768 encapsulate", _measure(repetitions, do_encapsulate),
                        wire=ct_size, public_key=len(public_bytes), ciphertext=ct_size,
                        note="Runtime-supported cryptography ML-KEM API; raw public key size is reported separately."),
            _timing_row("ML-KEM-768 decapsulate", _measure(repetitions, do_decapsulate),
                        wire=ct_size, public_key=len(public_bytes), ciphertext=ct_size,
                        note="Per operation; recipient already has the private key.")]
    return rows


def _bb84_rows(summary_csv: Path) -> list[dict[str, Any]]:
    with summary_csv.open(newline="", encoding="utf-8") as stream:
        summaries = list(csv.DictReader(stream))
    rows: list[dict[str, Any]] = []
    for sweep, title, parameter in (
        ("distance", "BB84 simulated distance", "distance_km"),
        ("eve", "BB84 simulated Eve fraction", "eve_fraction"),
    ):
        selected = [row for row in summaries if row["sweep"] == sweep]
        if not selected:
            raise ValueError(f"No {sweep} rows found in {summary_csv}")
        for item in selected:
            runs = int(item["runs"])
            rows.append({
                "category": "bb84_simulated", "method": title,
                "parameter": parameter, "parameter_value": item["parameter_value"],
                "runs": runs, "median_ms": "", "p95_ms": "", "bytes_on_wire": "",
                "public_key_bytes": "", "ciphertext_bytes": "",
                "signals_per_accepted_key_bit": item["signals_per_key_bit_mean"],
                "qber": item["qber_mean"], "acceptance_rate": item["acceptance_rate_mean"],
                "note": f"Simulated; summary.csv reports {runs} run(s) per point. No simulator wall-clock timing comparison.",
            })
    return rows


def _mitm_row() -> dict[str, Any]:
    # Eve replaces each unauthenticated ephemeral public key and establishes a
    # distinct X25519/HKDF secret with Alice and Bob.
    alice = x25519.X25519PrivateKey.generate()
    bob = x25519.X25519PrivateKey.generate()
    eve_to_alice = x25519.X25519PrivateKey.generate()
    eve_to_bob = x25519.X25519PrivateKey.generate()
    alice_eve = _hkdf(alice.exchange(eve_to_alice.public_key()))
    eve_alice = _hkdf(eve_to_alice.exchange(alice.public_key()))
    bob_eve = _hkdf(bob.exchange(eve_to_bob.public_key()))
    eve_bob = _hkdf(eve_to_bob.exchange(bob.public_key()))
    success = alice_eve == eve_alice and bob_eve == eve_bob and alice_eve != bob_eve
    if not success:
        raise AssertionError("In-memory X25519 MITM demonstration failed its key checks")
    return {
        "category": "attacker_demo", "method": "Unauthenticated X25519 MITM",
        "parameter": "attacker", "parameter_value": "Eve", "runs": 1,
        "median_ms": "", "p95_ms": "", "bytes_on_wire": "",
        "public_key_bytes": "", "ciphertext_bytes": "",
        "signals_per_accepted_key_bit": "", "qber": "", "acceptance_rate": "",
        "note": "Measured in-memory: Eve shares a key with Alice and another with Bob; endpoint check absent; Alice and Bob do not detect substitution.",
    }


def _plot(rows: list[dict[str, Any]], output: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("The PNG requires optional matplotlib; install requirements-experiments.txt") from exc

    classical = [row for row in rows if row["category"] == "classical_benchmark" and row["median_ms"] != ""]
    distance = [row for row in rows if row["category"] == "bb84_simulated" and row["parameter"] == "distance_km"]
    eve = [row for row in rows if row["category"] == "bb84_simulated" and row["parameter"] == "eve_fraction"]
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    operation_rows = [row for row in classical if row["method"] != "RSA-2048 key generation"]
    setup_rows = [row for row in classical if row["method"] == "RSA-2048 key generation"]
    timing_rows = operation_rows + setup_rows
    labels = [row["method"].replace(" (both sides)", "") for row in timing_rows]
    medians = [float(row["median_ms"]) for row in timing_rows]
    p95s = [float(row["p95_ms"]) for row in timing_rows]
    positions = list(range(len(timing_rows)))
    colors = ["#417b6a"] * len(operation_rows) + ["#c28a3e"] * len(setup_rows)
    axes[0, 0].barh(positions, medians, color=colors)
    axes[0, 0].scatter(p95s, positions, marker="D", color="#273b35", zorder=3, label="p95")
    for y, median in zip(positions, medians):
        axes[0, 0].annotate(f"{median:.3g}", (median, y), xytext=(4, 0),
                            textcoords="offset points", va="center", fontsize=8)
    axes[0, 0].set_yticks(positions, labels)
    axes[0, 0].invert_yaxis()
    if setup_rows:
        axes[0, 0].axhline(len(operation_rows) - .5, color="#777777", linestyle=":", linewidth=1)
        axes[0, 0].text(.02, .02, "RSA key generation: one-time setup", transform=axes[0, 0].transAxes,
                        fontsize=8, va="bottom", color="#73501e")
    axes[0, 0].set_xscale("log")
    axes[0, 0].set_title("Classical timing · log scale; key generation is setup", fontsize=12)
    axes[0, 0].set_xlabel("Milliseconds (bar = median with value; diamond = p95)")
    axes[0, 0].grid(axis="x", alpha=.2)

    byte_rows = [row for row in classical if row["bytes_on_wire"] != ""]
    byte_values = [int(row["bytes_on_wire"]) for row in byte_rows]
    byte_bars = axes[0, 1].barh([row["method"].replace(" (both sides)", "") for row in byte_rows],
                                byte_values, color="#657da3")
    for bar, value in zip(byte_bars, byte_values):
        axes[0, 1].annotate(f"{value} B", (value, bar.get_y() + bar.get_height() / 2),
                            xytext=(4, 0), textcoords="offset points", va="center", fontsize=8)
    axes[0, 1].margins(x=.18)
    axes[0, 1].set_title("Raw wire bytes per exchange · measured here", fontsize=12)
    axes[0, 1].set_xlabel("Bytes (protocol framing and TLS excluded)")
    axes[0, 1].grid(axis="x", alpha=.2)
    axes[0, 1].text(.01, .01, "RSA public key (294 B DER) is a separate one-time cost, not included.",
                    transform=axes[0, 1].transAxes, fontsize=7, va="bottom")

    if distance:
        axes[1, 0].plot([float(row["parameter_value"]) for row in distance],
                        [float(row["signals_per_accepted_key_bit"]) for row in distance],
                        marker="o", color="#89613b")
    axes[1, 0].set_title("BB84 signals per accepted key bit · simulated", fontsize=12)
    axes[1, 0].set_xlabel("Distance (km)")
    axes[1, 0].set_ylabel("Signals / accepted bit")
    axes[1, 0].grid(alpha=.2)

    if eve:
        fractions = [float(row["parameter_value"]) for row in eve]
        axes[1, 1].plot(fractions, [float(row["qber"]) * 100 for row in eve],
                        marker="o", color="#a74e45", label="QBER")
        axes[1, 1].axhline(11, color="#333333", linestyle="--", label="11% cutoff")
        axes[1, 1].set_ylabel("QBER (%)")
        axes[1, 1].set_xlabel("Eve intercept fraction")
        axes[1, 1].set_title("BB84 Eve sweep · simulated", fontsize=12)
        axes[1, 1].grid(alpha=.2)
        secondary = axes[1, 1].twinx()
        secondary.plot(fractions, [float(row["acceptance_rate"]) * 100 for row in eve],
                       marker="s", linestyle=":", color="#417b6a")
        secondary.set_ylabel("Accepted runs (%)")
        secondary.set_ylim(-5, 105)
        axes[1, 1].annotate("QBER", xy=(fractions[-1], float(eve[-1]["qber"]) * 100),
                            xytext=(-45, -12), textcoords="offset points", color="#a74e45", fontsize=8)
        axes[1, 1].annotate("11% cutoff", xy=(fractions[-1], 11), xytext=(-68, -14),
                            textcoords="offset points", color="#333333", fontsize=8)
        secondary.annotate("Accepted runs", xy=(fractions[-1], float(eve[-1]["acceptance_rate"]) * 100),
                           xytext=(-88, 9), textcoords="offset points", color="#417b6a", fontsize=8)
    fig.suptitle("Key establishment comparison · separate measures, no wall-clock cross-comparison", fontsize=14)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def run_comparison(repetitions: int, result_dir: Path, output_dir: Path) -> tuple[Path, Path]:
    if repetitions < 1:
        raise ValueError("--repetitions must be at least 1")
    summary = result_dir / "summary.csv"
    if not summary.is_file():
        raise FileNotFoundError(f"Existing BB84 summary not found: {summary}")
    rows = _classical_rows(repetitions)
    rows.extend(_mlkem_rows(repetitions))
    rows.extend(_bb84_rows(summary))
    rows.append(_mitm_row())
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "compare_key_establishment.csv"
    png_path = output_dir / "compare_key_establishment.png"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    _plot(rows, png_path)
    return csv_path, png_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=1000,
                        help="Repetitions for classical operations (default: 1000). RSA key generation uses up to 100.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS,
                        help="Directory containing the existing summary.csv")
    parser.add_argument("--out", type=Path, default=DEFAULT_RESULTS,
                        help="Directory for output CSV and PNG")
    parser.add_argument("--quick", action="store_true", help="Use five repetitions for a smoke run.")
    args = parser.parse_args()
    repetitions = min(args.repetitions, 5) if args.quick else args.repetitions
    try:
        csv_path, png_path = run_comparison(repetitions, args.results, args.out)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"Wrote {csv_path}")
    print(f"Wrote {png_path}")
    print(f"Classical operation repetitions: {repetitions}; RSA key-generation repetitions: {min(repetitions, RSA_KEYGEN_REPETITIONS)}")


if __name__ == "__main__":
    main()

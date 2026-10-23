"""SeQUeNCe BB84 + Cascade experiments used as the prototype key source."""

from __future__ import annotations

import hashlib
import secrets
import statistics
from dataclasses import dataclass
from typing import Any

from sequence.components.optical_channel import ClassicalChannel, QuantumChannel
from sequence.components.photon import Photon
from sequence.kernel.event import Event
from sequence.kernel.process import Process
from sequence.kernel.timeline import Timeline
from sequence.qkd.BB84 import pair_bb84_protocols
from sequence.qkd.cascade import pair_cascade_protocols
from sequence.topology.node import QKDNode


class EveQuantumChannel(QuantumChannel):
    """Quantum channel that applies intercept-resend to selected photons."""

    def __init__(self, *args: Any, intercept_fraction: float = 0.0, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.intercept_fraction = intercept_fraction
        self.photons_seen = 0
        self.photons_intercepted = 0

    def transmit(self, qubit: Any, source: Any) -> None:
        self.photons_seen += 1
        rng = self.get_generator()
        if (self.intercept_fraction > 0 and not qubit.is_null
                and rng.random() < self.intercept_fraction):
            bases = qubit.encoding_type["bases"]
            eve_basis = bases[int(rng.integers(0, 2))]
            Photon.measure(eve_basis, qubit, rng)
            self.photons_intercepted += 1
        super().transmit(qubit, source)


@dataclass(frozen=True)
class LinkSettings:
    distance_km: float = 10.0
    eve_fraction: float = 0.0
    channel_noise: float = 0.01


def _validate(settings: LinkSettings) -> None:
    if not 1 <= settings.distance_km <= 50:
        raise ValueError("Distance must be between 1 and 50 km.")
    if not 0 <= settings.eve_fraction <= 1:
        raise ValueError("Eve interception must be between 0 and 1.")
    if not 0 <= settings.channel_noise <= 0.15:
        raise ValueError("Channel noise must be between 0 and 0.15.")


def run_sequence_bb84(settings: LinkSettings, *, seed: int | None = None,
                      key_len: int = 256) -> dict[str, Any]:
    """Run one SeQUeNCe discrete-event BB84 and Cascade simulation.

    Cascade's reconciled key values are returned only when Alice and Bob have
    matching values and the observed BB84 QBER is below the UI's teaching
    cutoff. This experiment is not a finite-key security proof.
    """
    _validate(settings)
    if key_len != 256:
        raise ValueError("The messaging demo currently uses 256-bit keys.")

    run_seed = secrets.randbits(31) if seed is None else seed
    Timeline.seed(run_seed)
    timeline = Timeline(7e12)
    distance_m = settings.distance_km * 1000
    fidelity = max(0.5, min(0.999, 0.98 - settings.channel_noise))

    q0 = EveQuantumChannel(
        "qc0", timeline, distance=distance_m, polarization_fidelity=fidelity,
        attenuation=0.0002, intercept_fraction=settings.eve_fraction,
    )
    q1 = QuantumChannel(
        "qc1", timeline, distance=distance_m,
        polarization_fidelity=fidelity, attenuation=0.0002,
    )
    c0 = ClassicalChannel("cc0", timeline, distance=distance_m)
    c1 = ClassicalChannel("cc1", timeline, distance=distance_m)
    c0.delay += 1e9
    c1.delay += 1e9

    alice = QKDNode("alice", timeline, stack_size=2)
    alice.set_seed(run_seed + 1)
    alice.update_lightsource_params("frequency", 80e6)
    alice.update_lightsource_params("mean_photon_num", 0.1)

    bob = QKDNode("bob", timeline, stack_size=2)
    bob.set_seed(run_seed + 2)
    for detector in range(2):
        for param, value in {
            "efficiency": 0.8, "dark_count": 10,
            "time_resolution": 10, "count_rate": 50e6,
        }.items():
            bob.update_detector_params(detector, param, value)

    q0.set_ends(alice, bob.name)
    q1.set_ends(bob, alice.name)
    c0.set_ends(alice, bob.name)
    c1.set_ends(bob, alice.name)

    alice_bb84, bob_bb84 = alice.protocol_stack[0], bob.protocol_stack[0]
    pair_bb84_protocols(alice_bb84, bob_bb84)
    alice_cascade, bob_cascade = alice.protocol_stack[1], bob.protocol_stack[1]
    pair_cascade_protocols(alice_cascade, bob_cascade)

    timeline.schedule(Event(
        0, Process(alice_cascade, "push", [key_len, 1, 6e12])
    ))
    timeline.init()
    timeline.run()

    qber_values = list(alice_bb84.error_rates)
    qber = statistics.mean(qber_values) if qber_values else 1.0
    matching_keys: list[bytes] = []
    for a_key, b_key in zip(alice_cascade.valid_keys, bob_cascade.valid_keys):
        if a_key == b_key:
            matching_keys.append(a_key.to_bytes(key_len // 8, "big"))

    return {
        "simulator": "SeQUeNCe",
        "protocol": "BB84 + Cascade",
        "seed": run_seed,
        "distance_km": settings.distance_km,
        "eve_fraction": settings.eve_fraction,
        "channel_noise": settings.channel_noise,
        "polarization_fidelity": fidelity,
        "signals_sent": q0.photons_seen,
        "signals_intercepted": q0.photons_intercepted,
        "qber_samples": len(qber_values),
        "qber": qber,
        "cascade_keys_generated": len(alice_cascade.valid_keys),
        "matching_cascade_keys": len(matching_keys),
        "cascade_qber": alice_cascade.error_bit_rate,
        "cascade_throughput_bps": alice_cascade.throughput,
        "cascade_privacy_throughput_bps": alice_cascade.privacy_throughput,
        "accepted": bool(qber < 0.11 and matching_keys),
        "key_materials": matching_keys,
    }

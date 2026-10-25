"""Track a moving eavesdropper against QKD and classical key-exchange baselines."""

from __future__ import annotations

import argparse
import csv
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

QBER_CUTOFF = 0.11
DEFAULT_EVE_FRACTION = 0.75
# Allow synchronous SeQUeNCe key provisioning to finish on slower machines.
GENERATE_TIMEOUT = 900
INITIAL_STATE_RETRY_WINDOW = 120
INITIAL_STATE_RETRY_INTERVAL = 2
INITIAL_STATE_REQUEST_TIMEOUT = 8
LINKS = ("A-R1", "R1-B", "A-R2", "R2-B")
SCENARIOS = (
    ("clear", ()),
    ("eve_a_r1", ("A-R1",)),
    ("eve_r2_b", ("R2-B",)),
    ("eve_blocks_both_routes", ("A-R1", "R2-B")),
    ("eve_r1_b", ("R1-B",)),
    ("eve_a_r2", ("A-R2",)),
    ("eve_gone", ()),
)


def require_default_topology(state: dict[str, Any]) -> None:
    link_ids = {link.get("id") for link in state.get("links", [])}
    if not state.get("topology") or link_ids != set(LINKS):
        actual = ", ".join(sorted(str(link_id) for link_id in link_ids)) or "none"
        raise RuntimeError(
            f"experiments/moving_eve.py supports only config/topology.json (the four-link default); "
            f"the coordinator reported links: {actual}."
        )


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout: float = 8) -> dict[str, Any]:
    request = urllib.request.Request(base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def get_json(base_url: str, path: str, timeout: float = 8) -> dict[str, Any]:
    with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=timeout) as response:
        return json.loads(response.read())


def _is_retryable_initial_state_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 503
    reason = getattr(exc, "reason", None)
    return isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(reason, (TimeoutError, socket.timeout))


def _get_initial_state(base_url: str) -> dict[str, Any]:
    deadline = time.monotonic() + INITIAL_STATE_RETRY_WINDOW
    last_error: Exception | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            return get_json(base_url, "/api/state",
                            timeout=min(INITIAL_STATE_REQUEST_TIMEOUT, remaining))
        except ValueError as exc:
            raise classify_http_failure(exc, base_url) from exc
        except (OSError, urllib.error.URLError) as exc:
            if not _is_retryable_initial_state_error(exc):
                raise classify_http_failure(exc, base_url) from exc
            last_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(INITIAL_STATE_RETRY_INTERVAL, remaining))
    detail = classify_http_failure(last_error, base_url) if last_error else "No response was received."
    raise RuntimeError(f"Coordinator did not provide initial /api/state within {INITIAL_STATE_RETRY_WINDOW} seconds. It may still be generating keys. Details: {detail}")


def _restore_link_settings(base_url: str, links: list[dict[str, Any]]) -> list[str]:
    errors = []
    for link in links:
        link_id = link.get("id", "<unknown>")
        try:
            post_json(base_url, f"/api/links/{link_id}/settings", {
                "distance_km": link["distance_km"],
                "channel_noise": link["noise"],
                "eve_fraction": link["eve_fraction"] if "eve_fraction" in link else link["eve"],
            })
        except Exception as exc:
            errors.append(f"{link_id}: {exc}")
    return errors


def classify_http_failure(exc: Exception, base_url: str) -> RuntimeError:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = json.loads(exc.read())
            detail = body.get("error", str(exc))
        except (ValueError, OSError):
            detail = str(exc)
        return RuntimeError(f"Coordinator rejected the request at {base_url}: {detail}. Check that all node processes use the same QKD_PROVISION_TOKEN and topology ports.")
    reason = getattr(exc, "reason", None)
    if isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(reason, (TimeoutError, socket.timeout)):
        return RuntimeError(f"The coordinator at {base_url} is reachable, but the request timed out. Key generation can take several minutes; wait for it to finish and try again. Details: {exc}")
    if isinstance(exc, (urllib.error.URLError, ConnectionError)):
        return RuntimeError(f"Cannot reach coordinator at {base_url}. Start coordinator, Alice, Relay 1, Relay 2, and Bob using the README commands. Details: {exc}")
    return RuntimeError(f"The request to coordinator at {base_url} failed: {exc}")


def key_bytes(key: Any) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def derive(shared: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"moving-eve-baseline-v1").derive(shared)


def classical_hop_handshake(eve_present: bool, authenticated: bool) -> dict[str, Any]:
    """Perform X25519 exchanges; optionally authenticate transcript with Ed25519.

    In the unauthenticated MITM case Eve establishes one real exchange with each
    endpoint and relays traffic between distinct shared secrets. With signatures,
    the endpoints sign/verify the X25519 transcript using pinned signing keys.
    """
    alice = X25519PrivateKey.generate()
    bob = X25519PrivateKey.generate()
    alice_pub, bob_pub = key_bytes(alice.public_key()), key_bytes(bob.public_key())
    transcript = b"moving-eve-baseline-v1" + alice_pub + bob_pub
    signer = Ed25519PrivateKey.generate()
    signature = signer.sign(transcript)
    if eve_present and not authenticated:
        eve_to_alice = X25519PrivateKey.generate()
        eve_to_bob = X25519PrivateKey.generate()
        alice_secret = derive(alice.exchange(eve_to_alice.public_key()))
        eve_alice_secret = derive(eve_to_alice.exchange(alice.public_key()))
        bob_secret = derive(bob.exchange(eve_to_bob.public_key()))
        eve_bob_secret = derive(eve_to_bob.exchange(bob.public_key()))
        if alice_secret != eve_alice_secret or bob_secret != eve_bob_secret:
            raise AssertionError("The attacker and endpoint did not derive matching hop keys.")
        return {"established": True, "detected": False, "exposed": True,
                "alice_key": alice_secret, "bob_key": bob_secret}
    if eve_present:
        substitute = X25519PrivateKey.generate()
        substituted_transcript = b"moving-eve-baseline-v1" + alice_pub + key_bytes(substitute.public_key())
        try:
            signer.public_key().verify(signature, substituted_transcript)
        except Exception:
            return {"established": False, "detected": True, "exposed": False,
                    "alice_key": None, "bob_key": None}
        raise AssertionError("A substituted X25519 key unexpectedly verified.")
    signer.public_key().verify(signature, transcript)
    alice_secret = derive(alice.exchange(bob.public_key()))
    bob_secret = derive(bob.exchange(alice.public_key()))
    if alice_secret != bob_secret:
        raise AssertionError("X25519 peers did not derive the same baseline key.")
    return {"established": True, "detected": False, "exposed": False,
            "alice_key": alice_secret, "bob_key": bob_secret}


def message_exposed(path: list[str], accepted_eve_links: set[str], link_by_pair: dict[frozenset[str], dict[str, Any]]) -> bool:
    return any(link_by_pair[frozenset((a, b))]["id"] in accepted_eve_links
               for a, b in zip(path, path[1:]))


def _path_links(path: list[str], by_pair: dict[frozenset[str], dict[str, Any]]) -> list[str]:
    return [by_pair[frozenset((a, b))]["id"] for a, b in zip(path, path[1:])]


def _shortest(paths: list[list[str]], link_by_id: dict[str, dict[str, Any]], blocked: set[str] = frozenset()) -> list[str] | None:
    allowed = []
    for path in paths:
        ids = _path_links(path, {frozenset((item["a"], item["b"])): item for item in link_by_id.values()})
        if not set(ids) & blocked:
            allowed.append((sum(float(link_by_id[item]["distance_km"]) for item in ids), path))
    return min(allowed, default=(0, None), key=lambda item: item[0])[1]


def run_scenario(base_url: str, output: Path, messages: int, eve_fraction: float,
                 scenarios: tuple[tuple[str, tuple[str, ...]], ...] = SCENARIOS) -> list[dict[str, Any]]:
    initial = _get_initial_state(base_url)
    require_default_topology(initial)
    paths = initial.get("paths", [])
    rows: list[dict[str, Any]] = []
    try:
        for round_index, (name, eve_links) in enumerate(scenarios):
            post_json(base_url, "/api/reset", {})
            for link_id in LINKS:
                link = next((item for item in initial["links"] if item["id"] == link_id), None)
                if link is None:
                    raise RuntimeError(f"Coordinator topology is missing required link {link_id}.")
                post_json(base_url, f"/api/links/{link_id}/settings", {
                    "distance_km": link["distance_km"], "channel_noise": link["noise"],
                    "eve_fraction": eve_fraction if link_id in eve_links else 0.0,
                })
            post_json(base_url, "/api/keys/generate", {"count": 24}, timeout=GENERATE_TIMEOUT)
            state = get_json(base_url, "/api/state")
            link_by_id = {link["id"]: link for link in state["links"]}
            pair_map = {frozenset((item["a"], item["b"])): item for item in state["links"]}
            accepted_eve = {item["id"] for item in state["links"]
                            if item["id"] in eve_links and item["pool_count"] > 0}
            qkd_paths: list[str] = []
            qkd_delivered = qkd_blocked = qkd_exposed = 0
            for message_index in range(messages):
                try:
                    result = post_json(base_url, "/api/messages", {"text": f"moving eve {round_index}-{message_index}"})
                    delivered = result.get("status") == "delivered"
                    qkd_delivered += int(delivered)
                    qkd_blocked += int(not delivered)
                    path = result.get("path", [])
                    if path:
                        qkd_paths.append(">".join(path))
                        qkd_exposed += int(delivered and message_exposed(path, accepted_eve, pair_map))
                except urllib.error.HTTPError:
                    qkd_blocked += 1
            after = get_json(base_url, "/api/state")
            paths = after.get("paths", paths)
            links_after = {item["id"]: item for item in after["links"]}

            # Classical exchanges run once per hop/link and are reused for this round's message counts.
            unauth_hops = {link_id: classical_hop_handshake(link_id in eve_links, False) for link_id in LINKS}
            auth_hops = {link_id: classical_hop_handshake(link_id in eve_links, True) for link_id in LINKS}
            pair_after = {frozenset((item["a"], item["b"])): item for item in after["links"]}
            unauth_path = _shortest(paths, links_after)
            blocked_auth = {link_id for link_id, result in auth_hops.items() if not result["established"]}
            auth_path = _shortest(paths, links_after, blocked_auth)
            unauth_delivered = messages if unauth_path else 0
            auth_delivered = messages if auth_path else 0
            unauth_exposed = sum(message_exposed(unauth_path, set(eve_links), pair_after)
                                 for _ in range(messages)) if unauth_path else 0
            row: dict[str, Any] = {
                "record_type": "round", "round": round_index, "scenario": name,
                "eve_links": ";".join(eve_links), "eve_fraction": eve_fraction,
                "qkd_paths": ";".join(sorted(set(qkd_paths))) or "blocked",
                "qkd_delivered": qkd_delivered, "qkd_blocked": qkd_blocked,
                "qkd_exposed": qkd_exposed,
                "classical_unauth_path": ">".join(unauth_path) if unauth_path else "blocked",
                "classical_unauth_delivered": unauth_delivered,
                "classical_unauth_blocked": messages - unauth_delivered,
                "classical_unauth_exposed": unauth_exposed,
                "classical_auth_path": ">".join(auth_path) if auth_path else "blocked",
                "classical_auth_delivered": auth_delivered,
                "classical_auth_blocked": messages - auth_delivered,
                "classical_auth_exposed": 0,
            }
            for link_id in LINKS:
                link = links_after[link_id]
                row[f"{link_id}_qber"] = link["qber"]
                row[f"{link_id}_accepted"] = bool(link["pool_count"] > 0)
                row[f"{link_id}_pool_count"] = link["pool_count"]
            rows.append(row)

        # Additional one-link intercept-fraction curve, restoring clear settings on other links.
        for index, fraction in enumerate((0.1, 0.25, 0.5, 0.75, 1.0)):
            post_json(base_url, "/api/reset", {})
            for link in initial["links"]:
                post_json(base_url, f"/api/links/{link['id']}/settings", {
                    "distance_km": link["distance_km"], "channel_noise": link["noise"],
                    "eve_fraction": fraction if link["id"] == "A-R1" else 0.0,
                })
            post_json(base_url, "/api/keys/generate", {"count": 24}, timeout=GENERATE_TIMEOUT)
            sweep_state = get_json(base_url, "/api/state")
            sweep_links = {item["id"]: item for item in sweep_state["links"]}
            link = sweep_links["A-R1"]
            rows.append({"record_type": "intercept_sweep", "round": len(scenarios) + index,
                         "scenario": "intercept_fraction", "eve_links": "A-R1",
                         "eve_fraction": fraction, "qkd_paths": "", "qkd_delivered": 0,
                         "qkd_blocked": 0, "qkd_exposed": 0,
                         "classical_unauth_path": "", "classical_unauth_delivered": 0,
                         "classical_unauth_blocked": 0, "classical_unauth_exposed": 0,
                         "classical_auth_path": "", "classical_auth_delivered": 0,
                         "classical_auth_blocked": 0, "classical_auth_exposed": 0,
                         **{f"{link_id}_{field}": (sweep_links[link_id].get("qber") if field == "qber" else
                              bool(sweep_links[link_id]["pool_count"] > 0) if field == "accepted" else
                              sweep_links[link_id]["pool_count"])
                            for link_id in LINKS for field in ("qber", "accepted", "pool_count")}})
    except (OSError, ValueError, urllib.error.URLError, KeyError) as exc:
        raise classify_http_failure(exc, base_url) from exc
    finally:
        restore_errors = _restore_link_settings(base_url, initial.get("links", []))
        if restore_errors:
            restoration_failure = RuntimeError("Could not restore original link settings: " + "; ".join(restore_errors))
            active_error = sys.exc_info()[1]
            if active_error is not None:
                active_error.add_note(str(restoration_failure))
            else:
                raise restoration_failure
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "moving_eve.csv"
    columns = ["record_type", "round", "scenario", "eve_links", "eve_fraction", "qkd_paths",
               "qkd_delivered", "qkd_blocked", "qkd_exposed", "classical_unauth_path",
               "classical_unauth_delivered", "classical_unauth_blocked", "classical_unauth_exposed",
               "classical_auth_path", "classical_auth_delivered", "classical_auth_blocked",
               "classical_auth_exposed"]
    columns += [f"{link}_{field}" for link in LINKS for field in ("qber", "accepted", "pool_count")]
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    _plot(rows, output / "moving_eve_timeline.png")
    return rows


def _plot(rows: list[dict[str, Any]], destination: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("The timeline PNG requires optional matplotlib. Install requirements-experiments.txt.") from exc
    rounds = [row for row in rows if row["record_type"] == "round"]
    labels = [str(row["round"]) for row in rounds]
    fig, (qber_ax, path_ax, count_ax) = plt.subplots(3, 1, figsize=(11, 8),
                                                      gridspec_kw={"height_ratios": [1.2, .12, 1.25]})
    for link_id in LINKS:
        qber_ax.plot(labels, [float(row[f"{link_id}_qber"] or 0) * 100 for row in rounds], marker="o", label=link_id)
    qber_ax.axhline(QBER_CUTOFF * 100, color="black", linestyle="--", linewidth=1, label="11% cutoff")
    qber_ax.set_ylabel("QBER (%)")
    qber_ax.set_title("Moving eavesdropper: link error rate and route outcomes")
    qber_ax.legend(ncol=5, fontsize=8)
    qber_ax.grid(alpha=.2)
    path_ax.axis("off")
    path_ax.set_title("QKD path used per round", pad=1, fontsize=9)
    for index, row in enumerate(rounds):
        path_ax.text(index / max(1, len(rounds) - 1), .5, f"{row['round']}: {row['qkd_paths']}",
                     ha="center", va="center", fontsize=6.5, transform=path_ax.transAxes)
    x = list(range(len(rounds)))
    width = .24
    for offset, (prefix, label, color) in enumerate((
        ("qkd", "QKD", "#347a54"), ("classical_unauth", "Classical · unauthenticated", "#c5892c"),
        ("classical_auth", "Classical · signed", "#426e9b"))):
        count_ax.bar([value + (offset - 1) * width for value in x],
                     [row[f"{prefix}_delivered"] for row in rounds], width, label=f"{label}: delivered", color=color)
        count_ax.bar([value + (offset - 1) * width for value in x],
                     [row[f"{prefix}_blocked"] for row in rounds], width, bottom=[row[f"{prefix}_delivered"] for row in rounds],
                     alpha=.28, color=color, label=f"{label}: blocked")
    count_ax.plot(x, [row["qkd_exposed"] for row in rounds], color="#9c3f38", marker="D", linestyle="--", label="QKD exposed")
    count_ax.plot(x, [row["classical_unauth_exposed"] for row in rounds], color="#6a4a91", marker="x", linestyle=":", label="Classical unauth exposed")
    count_ax.set_xticks(x, labels)
    count_ax.set_xlabel("Scenario round")
    count_ax.set_ylabel("Messages")
    handles, legend_labels = count_ax.get_legend_handles_labels()
    count_ax.grid(axis="y", alpha=.18)
    fig.legend(handles, legend_labels, loc="lower center", bbox_to_anchor=(.5, .01),
               ncol=3, fontsize=7)
    fig.tight_layout(rect=(0, .07, 1, 1), h_pad=.45)
    fig.savefig(destination, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--out", type=Path, default=Path("experiments/results"))
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--messages", type=int, default=5)
    parser.add_argument("--eve-fraction", type=float, default=DEFAULT_EVE_FRACTION)
    parser.add_argument("--quick", action="store_true", help="Run three scenarios with two messages per round.")
    args = parser.parse_args()
    if not (0 <= args.eve_fraction <= 1) or args.messages < 1 or not 1 <= args.rounds <= len(SCENARIOS):
        parser.error(f"rounds must be 1–{len(SCENARIOS)}, messages must be positive, and Eve fraction must be in [0, 1].")
    scenarios = SCENARIOS[:3] if args.quick else SCENARIOS[:args.rounds]
    messages = min(args.messages, 2) if args.quick else args.messages
    try:
        rows = run_scenario(args.base_url, args.out, messages, args.eve_fraction, scenarios)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Wrote {args.out / 'moving_eve.csv'} ({len(rows)} rows)")
    print(f"Wrote {args.out / 'moving_eve_timeline.png'}")
    print(f"Full scenario: {len(scenarios)} rounds × {messages} messages, plus five intercept-fraction runs.")


if __name__ == "__main__":
    main()

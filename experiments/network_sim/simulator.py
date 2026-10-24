"""Standard-library network simulator driven by the checked-in BB84 pilot data."""

from __future__ import annotations

import csv
import heapq
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

KEY_BITS = 256
QBER_CUTOFF = 0.11
TOPOLOGY_SEED = 20261024
POOL_CAPACITY_KEYS = 32
POOL_CAPACITY_BITS = KEY_BITS * POOL_CAPACITY_KEYS
USER_PAIRS = 4
POLICIES = ("fewest_hops", "shortest_distance", "key_aware")


@dataclass(frozen=True)
class Link:
    link_id: str
    a: str
    b: str
    distance_km: float


@dataclass
class Topology:
    name: str
    nodes: tuple[str, ...]
    links: tuple[Link, ...]
    pairs: tuple[tuple[str, str], ...]


class LinkModel:
    """Interpolates mean signals/key-bit and QBER from experiments/results/summary.csv."""

    def __init__(self, summary_path: Path, assumed_pulse_rate: float = 1e6):
        if assumed_pulse_rate <= 0:
            raise ValueError("assumed_pulse_rate must be positive")
        self.assumed_pulse_rate = float(assumed_pulse_rate)
        with summary_path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.distance_signal = sorted(
            (float(row["parameter_value"]), float(row["signals_per_key_bit_mean"]))
            for row in rows if row["sweep"] == "distance" and row["signals_per_key_bit_mean"]
        )
        self.distance_qber = sorted(
            (float(row["parameter_value"]), float(row["qber_mean"]))
            for row in rows if row["sweep"] == "distance"
        )
        self.eve_qber = sorted(
            (float(row["parameter_value"]), float(row["qber_mean"]))
            for row in rows if row["sweep"] == "eve"
        )
        if not self.distance_signal or not self.distance_qber or not self.eve_qber:
            raise ValueError(f"Incomplete distance/Eve curves in {summary_path}")
        self.min_distance = self.distance_signal[0][0]
        self.max_distance = self.distance_signal[-1][0]

    @staticmethod
    def _interpolate(points: list[tuple[float, float]], value: float) -> float | None:
        if value < points[0][0] or value > points[-1][0]:
            return None
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if value == x0:
                return y0
            if x0 <= value <= x1:
                ratio = (value - x0) / (x1 - x0)
                return y0 + ratio * (y1 - y0)
        return points[-1][1]

    def link_parameters(self, distance_km: float, *, attacker_present: bool = False) -> tuple[float | None, float | None, float]:
        signals_per_bit = self._interpolate(self.distance_signal, distance_km)
        qber = self._interpolate(self.distance_qber, distance_km)
        if signals_per_bit is None or qber is None:
            return None, None, 0.0
        if attacker_present:
            full_intercept_qber = self._interpolate(self.eve_qber, 1.0)
            if full_intercept_qber is None:
                raise ValueError("Eve curve does not include full interception")
            qber = max(qber, full_intercept_qber)
        rate = 0.0 if qber >= QBER_CUTOFF else self.assumed_pulse_rate / signals_per_bit
        return signals_per_bit, qber, rate


def interpolate(points: list[tuple[float, float]], x: float) -> float | None:
    """Public helper for testing the data-driven link model."""
    return LinkModel._interpolate(points, x)


def _distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    return max(1.0, math.hypot(p1[0] - p2[0], p1[1] - p2[1]))


def _components(nodes: Iterable[str], links: Iterable[Link]) -> list[set[str]]:
    adjacency = {node: set() for node in nodes}
    for edge in links:
        adjacency[edge.a].add(edge.b)
        adjacency[edge.b].add(edge.a)
    components = []
    remaining = set(adjacency)
    while remaining:
        start = min(remaining)
        seen = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current] - seen:
                seen.add(neighbor)
                stack.append(neighbor)
        components.append(seen)
        remaining -= seen
    return components


def generate_topologies(seed: int = TOPOLOGY_SEED) -> dict[str, Topology]:
    """Create a validation diamond, ring, grid, and seeded random-geometric mesh."""
    diamond_nodes = ("alice", "r1", "r2", "bob")
    diamond_links = tuple(Link(link_id, a, b, km) for link_id, a, b, km in (
        ("A-R1", "alice", "r1", 8.0), ("R1-B", "r1", "bob", 7.0),
        ("A-R2", "alice", "r2", 10.0), ("R2-B", "r2", "bob", 9.0),
    ))
    diamond = Topology("diamond", diamond_nodes, diamond_links, (("alice", "bob"),))

    ring_nodes = tuple(f"r{i:02d}" for i in range(12))
    radius = 22.0
    ring_pos = {node: (radius * math.cos(2 * math.pi * i / 12),
                       radius * math.sin(2 * math.pi * i / 12)) for i, node in enumerate(ring_nodes)}
    ring_links = tuple(Link(f"ring-{i:02d}", ring_nodes[i], ring_nodes[(i + 1) % 12],
                            _distance(ring_pos[ring_nodes[i]], ring_pos[ring_nodes[(i + 1) % 12]]))
                        for i in range(12))
    ring_pairs = (("r00", "r06"), ("r02", "r08"), ("r04", "r10"), ("r01", "r07"))
    ring = Topology("ring12", ring_nodes, ring_links, ring_pairs)

    grid_nodes = tuple(f"g{row}{col}" for row in range(4) for col in range(4))
    grid_pos = {f"g{row}{col}": (col * 8.0, row * 8.0) for row in range(4) for col in range(4)}
    grid_edges = []
    for row in range(4):
        for col in range(4):
            node = f"g{row}{col}"
            for dr, dc in ((0, 1), (1, 0)):
                nr, nc = row + dr, col + dc
                if nr < 4 and nc < 4:
                    other = f"g{nr}{nc}"
                    grid_edges.append(Link(f"grid-{node}-{other}", node, other,
                                           _distance(grid_pos[node], grid_pos[other])))
    grid_pairs = (("g00", "g33"), ("g03", "g30"), ("g01", "g32"), ("g10", "g23"))
    grid = Topology("grid4x4", grid_nodes, tuple(grid_edges), grid_pairs)

    rng = random.Random(seed)
    mesh_nodes = tuple(f"m{i:02d}" for i in range(15))
    mesh_pos = {node: (rng.uniform(0, 38), rng.uniform(0, 38)) for node in mesh_nodes}
    candidate_links = []
    for i, a in enumerate(mesh_nodes):
        for b in mesh_nodes[i + 1:]:
            d = _distance(mesh_pos[a], mesh_pos[b])
            if d <= 11.0:
                candidate_links.append(Link(f"mesh-{a}-{b}", a, b, d))
    # Join disconnected geometric components by their shortest inter-component edges.
    while len(_components(mesh_nodes, candidate_links)) > 1:
        components = _components(mesh_nodes, candidate_links)
        bridge = min((edge for edge in (
            Link(f"mesh-{a}-{b}", a, b, _distance(mesh_pos[a], mesh_pos[b]))
            for a in mesh_nodes for b in mesh_nodes if a < b
            if any(a in comp for comp in components) and any(b in comp for comp in components)
            if next(i for i, comp in enumerate(components) if a in comp)
            != next(i for i, comp in enumerate(components) if b in comp)
        )), key=lambda edge: edge.distance_km)
        candidate_links.append(bridge)
    mesh_pairs = (("m00", "m14"), ("m01", "m13"), ("m02", "m12"), ("m03", "m11"))
    mesh = Topology("metro15", mesh_nodes, tuple(candidate_links), mesh_pairs)
    return {topology.name: topology for topology in (diamond, ring, grid, mesh)}


def adjacency(topology: Topology, *, disabled: frozenset[str] = frozenset(),
              pools: dict[str, float] | None = None, key_aware: bool = False) -> dict[str, list[tuple[str, Link]]]:
    graph = {node: [] for node in topology.nodes}
    for edge in topology.links:
        if edge.link_id in disabled:
            continue
        if key_aware and pools is not None and pools.get(edge.link_id, 0) < KEY_BITS:
            continue
        graph[edge.a].append((edge.b, edge))
        graph[edge.b].append((edge.a, edge))
    for node in graph:
        graph[node].sort(key=lambda item: (item[0], item[1].link_id))
    return graph


def find_path(topology: Topology, source: str, target: str, policy: str,
              pools: dict[str, float] | None = None,
              disabled: frozenset[str] = frozenset()) -> list[str] | None:
    if policy not in POLICIES:
        raise ValueError(f"Unknown routing policy: {policy}")
    graph = adjacency(topology, disabled=disabled, pools=pools, key_aware=policy == "key_aware")
    queue: list[tuple[float, str, tuple[str, ...]]] = [(0.0, source, (source,))]
    best = {source: 0.0}
    while queue:
        cost, node, path = heapq.heappop(queue)
        if node == target:
            return list(path)
        if cost > best.get(node, math.inf):
            continue
        for neighbor, edge in graph[node]:
            if policy == "fewest_hops":
                weight = 1.0
            elif policy == "shortest_distance":
                weight = edge.distance_km
            else:
                fill = min(1.0, max(0.0, (pools or {}).get(edge.link_id, 0.0) / POOL_CAPACITY_BITS))
                weight = 1.0 + 8.0 * (1.0 - fill)
            new_cost = cost + weight
            if new_cost < best.get(neighbor, math.inf):
                best[neighbor] = new_cost
                heapq.heappush(queue, (new_cost, neighbor, (*path, neighbor)))
    return None


def poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    threshold = math.exp(-mean)
    product = 1.0
    count = 0
    while product > threshold:
        count += 1
        product *= rng.random()
    return count - 1


def refill_pool(current_bits: float, rate_bits_per_s: float, elapsed_s: float,
                capacity_bits: float = POOL_CAPACITY_BITS) -> float:
    return min(capacity_bits, current_bits + max(0.0, rate_bits_per_s) * elapsed_s)


def consume_path(pools: dict[str, float], path: list[str],
                 link_by_pair: dict[frozenset[str], Link], amount_bits: int = KEY_BITS) -> bool:
    required = [link_by_pair[frozenset((a, b))].link_id for a, b in zip(path, path[1:])]
    if any(pools.get(link_id, 0.0) < amount_bits for link_id in required):
        return False
    for link_id in required:
        pools[link_id] -= amount_bits
    return True


def connectivity_fraction(topology: Topology, pairs: tuple[tuple[str, str], ...],
                          disabled: frozenset[str]) -> float:
    connected = sum(find_path(topology, a, b, "fewest_hops", disabled=disabled) is not None for a, b in pairs)
    return connected / len(pairs) if pairs else 0.0


def attacker_schedule(topology: Topology, width: int, *, seed: int,
                      epochs: int = 6, steps_per_epoch: int = 10) -> dict[int, frozenset[str]]:
    if width < 1 or width > len(topology.links):
        raise ValueError("attacker width must be within the number of links")
    rng = random.Random(seed)
    links = {edge.link_id: edge for edge in topology.links}
    ids = sorted(links)
    neighbors = {link_id: set() for link_id in ids}
    for left in topology.links:
        for right in topology.links:
            if left.link_id != right.link_id and {left.a, left.b} & {right.a, right.b}:
                neighbors[left.link_id].add(right.link_id)
    positions = set(rng.sample(ids, width))
    result: dict[int, frozenset[str]] = {}
    for epoch in range(epochs):
        for tick in range(steps_per_epoch):
            result[epoch * steps_per_epoch + tick] = frozenset(positions)
        if epoch + 1 == epochs:
            break
        moved: set[str] = set()
        for old in sorted(positions):
            available = (neighbors[old] - positions) - moved
            if not available:
                available = set(ids) - positions - moved
            if available:
                moved.add(rng.choice(sorted(available)))
        positions = moved if len(moved) == width else set(rng.sample(ids, width))
    return result


def simulate(topology: Topology, model: LinkModel, policy: str, aggregate_rate: float,
             seed: int, *, steps: int = 60, step_seconds: float = 1.0,
             disabled: frozenset[str] = frozenset(),
             attack_by_step: dict[int, frozenset[str]] | None = None,
             initial_pool_keys: int = POOL_CAPACITY_KEYS) -> dict[str, object]:
    if not 0 <= initial_pool_keys <= POOL_CAPACITY_KEYS:
        raise ValueError("initial_pool_keys must fit within the configured pool capacity")
    rng = random.Random(seed)
    link_by_pair = {frozenset((edge.a, edge.b)): edge for edge in topology.links}
    rates = {edge.link_id: model.link_parameters(edge.distance_km)[2] for edge in topology.links}
    pools = {edge.link_id: float(KEY_BITS * initial_pool_keys) for edge in topology.links}
    total_requests = total_served = crossed_attacked = exposed = 0
    relay_visits = {node: 0 for node in topology.nodes}
    step_stats: list[tuple[int, int, int]] = []
    recovery_started: dict[str, int] = {}
    recovery_times: list[float] = []
    previous_attacks: frozenset[str] = frozenset()
    attack_by_step = attack_by_step or {}

    for step in range(steps):
        attacks = attack_by_step.get(step, frozenset())
        for link_id in previous_attacks - attacks:
            recovery_started[link_id] = step
        pre_refill_pools = dict(pools)
        for edge in topology.links:
            if edge.link_id in disabled or edge.link_id in attacks:
                continue
            pools[edge.link_id] = refill_pool(pools[edge.link_id], rates[edge.link_id], step_seconds)
        for link_id, start in list(recovery_started.items()):
            if pools.get(link_id, 0.0) >= KEY_BITS:
                recovery_times.append(0.0 if pre_refill_pools.get(link_id, 0.0) >= KEY_BITS
                                      else (step - start + 1) * step_seconds)
                del recovery_started[link_id]

        arrivals: list[tuple[str, str]] = []
        per_pair = aggregate_rate / len(topology.pairs) * step_seconds
        for pair in topology.pairs:
            arrivals.extend([pair] * poisson(rng, per_pair))
        rng.shuffle(arrivals)
        step_served = 0
        step_crossed = 0
        for source, target in arrivals:
            total_requests += 1
            path = find_path(topology, source, target, policy, pools, disabled)
            if path is None:
                continue
            edge_ids = [link_by_pair[frozenset((a, b))].link_id for a, b in zip(path, path[1:])]
            used_attacked = any(link_id in attacks for link_id in edge_ids)
            if not consume_path(pools, path, link_by_pair):
                continue
            total_served += 1
            step_served += 1
            if used_attacked:
                crossed_attacked += 1
                step_crossed += 1
            # Full interception is modeled above cutoff; it yields no accepted keys.
            # Existing pool keys predate the attack and are not attributed as exposed.
            for relay in path[1:-1]:
                relay_visits[relay] += 1
        step_stats.append((len(arrivals), step_served, step_crossed))
        previous_attacks = attacks

    return {
        "requests": total_requests,
        "served": total_served,
        "served_fraction": total_served / total_requests if total_requests else 0.0,
        "relay_visits": relay_visits,
        "step_stats": step_stats,
        "requests_crossing_attacked_links": crossed_attacked,
        "requests_exposed_to_eve": exposed,
        "recovery_times_s": recovery_times,
        "final_pools": pools,
    }

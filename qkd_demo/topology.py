"""Loading and validation for localhost relay topology configurations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TopologyError(ValueError):
    """A topology file is malformed or cannot be run on localhost."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TopologyError(f"Duplicate JSON object key {key!r}.")
        result[key] = value
    return result


def loads_topology(source: str) -> dict[str, Any]:
    try:
        topology = json.loads(source, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, TopologyError) as exc:
        raise TopologyError(str(exc)) from exc
    return validate_topology(topology)


def load_topology(path: str | Path) -> dict[str, Any]:
    topology_path = Path(path)
    try:
        source = topology_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TopologyError(f"Could not read topology file {topology_path}: {exc}") from exc
    try:
        return loads_topology(source)
    except TopologyError as exc:
        raise TopologyError(f"Invalid topology file {topology_path}: {exc}") from exc


def validate_topology(topology: Any) -> dict[str, Any]:
    if not isinstance(topology, dict):
        raise TopologyError("Topology root must be a JSON object.")
    nodes = topology.get("nodes")
    links = topology.get("links")
    if not isinstance(nodes, dict) or not nodes:
        raise TopologyError("Topology must contain a non-empty 'nodes' object.")
    if not isinstance(links, list):
        raise TopologyError("Topology must contain a 'links' array.")
    if "alice" not in nodes:
        raise TopologyError("Topology must define node 'alice'.")
    if "bob" not in nodes:
        raise TopologyError("Topology must define node 'bob'.")

    ports: dict[int, tuple[str, str]] = {}
    for node_id, config in nodes.items():
        if not isinstance(node_id, str) or not node_id:
            raise TopologyError("Node IDs must be non-empty strings.")
        if not isinstance(config, dict):
            raise TopologyError(f"Node {node_id!r} must have an object configuration.")
        for kind in ("http_port", "app_port"):
            port = config.get(kind)
            if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
                raise TopologyError(f"Node {node_id!r} {kind} must be an integer from 1024 through 65535.")
            if port in ports:
                other_id, other_kind = ports[port]
                raise TopologyError(
                    f"Port {port} is shared by node {other_id!r} {other_kind} and node {node_id!r} {kind}."
                )
            ports[port] = (node_id, kind)

    link_ids: set[str] = set()
    graph: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for index, link in enumerate(links):
        if not isinstance(link, dict):
            raise TopologyError(f"Link at index {index} must be an object.")
        link_id = link.get("id")
        if not isinstance(link_id, str) or not link_id:
            raise TopologyError(f"Link at index {index} must have a non-empty string ID.")
        if link_id in link_ids:
            raise TopologyError(f"Duplicate link ID {link_id!r}.")
        link_ids.add(link_id)
        left, right = link.get("a"), link.get("b")
        if left not in nodes:
            raise TopologyError(f"Link {link_id!r} references unknown node {left!r}.")
        if right not in nodes:
            raise TopologyError(f"Link {link_id!r} references unknown node {right!r}.")
        if left == right:
            raise TopologyError(f"Link {link_id!r} must connect two different nodes.")
        graph[left].append(right)
        graph[right].append(left)

    reached = {"alice"}
    pending = ["alice"]
    while pending:
        node_id = pending.pop()
        for neighbour in graph[node_id]:
            if neighbour not in reached:
                reached.add(neighbour)
                pending.append(neighbour)
    if "bob" not in reached:
        raise TopologyError("Alice and Bob are not connected by any path.")
    return topology

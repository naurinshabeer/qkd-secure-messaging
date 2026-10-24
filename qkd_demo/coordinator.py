"""Coordinator and web dashboard: runs simulations, provisions both KMEs, and relays UI calls."""

from __future__ import annotations

import json
import base64
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from qkd_demo.node import json_request
from qkd_demo.sequence_link import LinkSettings, run_sequence_bb84

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
POOL_LIMIT = 24
QBER_LIMIT = 0.11


def enumerate_paths(topology: dict[str, Any], source: str = "alice", target: str = "bob") -> list[list[str]]:
    """Enumerate simple node paths from Alice to Bob from the configured links."""
    graph: dict[str, list[str]] = {node_id: [] for node_id in topology["nodes"]}
    for link in topology["links"]:
        graph[link["a"]].append(link["b"])
        graph[link["b"]].append(link["a"])
    paths: list[list[str]] = []

    def walk(node_id: str, path: list[str]) -> None:
        if node_id == target:
            paths.append(path)
            return
        for neighbor in graph.get(node_id, []):
            if neighbor not in path:
                walk(neighbor, path + [neighbor])

    if source in graph and target in graph:
        walk(source, [source])
    return paths


class CoordinatorError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class LinkState:
    """Coordinator-owned switch for the simulated Alice-Bob classical/quantum link."""
    def __init__(self, up: bool = True, links: dict[str, bool] | None = None):
        self._up = up
        self._links = dict(links or {})
        self._lock = threading.Lock()

    def get(self, link_id: str | None = None) -> bool:
        with self._lock:
            return self._links.get(link_id, self._up) if link_id else self._up

    def set(self, up: bool, link_id: str | None = None) -> None:
        with self._lock:
            if link_id:
                self._links[link_id] = up
            else:
                self._up = up


class CoordinatorService:
    """Owns experiment metadata only; Alice and Bob retain separate KME pools."""

    def __init__(self, alice_url: str, bob_url: str, topology: dict[str, Any] | None = None):
        self.topology = topology
        if topology:
            self.node_urls = {node_id: f"http://127.0.0.1:{int(node['http_port'])}"
                              for node_id, node in topology["nodes"].items()}
            links = topology["links"]
            self.paths = enumerate_paths(topology)
            self.link_settings = {item["id"]: LinkSettings(float(item["distance_km"]),
                                  float(item.get("eve", 0.0)), float(item.get("noise", 0.01))) for item in links}
            self.link_state = LinkState(True, {item["id"]: bool(item.get("up", True)) for item in links})
            self.link_results: dict[str, dict[str, Any]] = {}
        else:
            self.node_urls = {"alice": alice_url.rstrip("/"), "bob": bob_url.rstrip("/")}
            self.paths = []
            self.link_settings = {}
            self.link_results = {}
        self.settings = LinkSettings()
        if not topology:
            self.link_state = LinkState(True)
        self.last_run: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.sent_count = 0
        self.blocked_count = 0
        self.last_path: list[str] | None = None
        self.started_at = time.time()
        self._lock = threading.RLock()
        self._event("Coordinator ready · independent Alice and Bob nodes", "info")

    @property
    def link_up(self) -> bool:
        return self.link_state.get()

    @link_up.setter
    def link_up(self, up: bool) -> None:
        self.link_state.set(up)

    def _event(self, message: str, level: str) -> None:
        self.events.insert(0, {"id": uuid.uuid4().hex[:8], "time": time.time(),
                               "message": message, "level": level})
        del self.events[30:]

    def _node(self, role: str, path: str, method: str = "GET",
              payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return json_request(self.node_urls[role] + path, method, payload)
        except RuntimeError as exc:
            text = str(exc)
            status = 503
            if text.startswith("HTTP "):
                try:
                    status = int(text.split()[1].rstrip(":"))
                except (ValueError, IndexError):
                    pass
            raise CoordinatorError(status, f"{role.title()} node unavailable: {text}") from exc

    def set_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self._validated_settings(payload, self.settings)
        with self._lock:
            self.settings = settings
            self._event("Next link run settings updated", "info")
            return self.state()

    @staticmethod
    def _validated_settings(payload: dict[str, Any], current: LinkSettings) -> LinkSettings:
        try:
            settings = LinkSettings(
                float(payload.get("distance_km", current.distance_km)),
                float(payload.get("eve_fraction", current.eve_fraction)),
                float(payload.get("channel_noise", current.channel_noise)),
            )
            # Validate without paying for a simulator run.
            if not 1 <= settings.distance_km <= 50:
                raise ValueError("Distance must be between 1 and 50 km.")
            if not 0 <= settings.eve_fraction <= 1:
                raise ValueError("Eve interception must be between 0 and 1.")
            if not 0 <= settings.channel_noise <= 0.15:
                raise ValueError("Channel noise must be between 0 and 0.15.")
        except (TypeError, OverflowError) as exc:
            raise ValueError("Settings must be numeric.") from exc

        return settings

    def set_link_settings(self, link_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if link_id not in self.link_settings:
            raise CoordinatorError(404, "Unknown topology link.")
        settings = self._validated_settings(payload, self.link_settings[link_id])
        with self._lock:
            self.link_settings[link_id] = settings
            self._event(f"{link_id} settings updated", "info")
            return self.state()

    def generate(self, requested_count: int = POOL_LIMIT) -> dict[str, Any]:
        if not isinstance(requested_count, int) or not 1 <= requested_count <= POOL_LIMIT:
            raise ValueError(f"Import count must be between 1 and {POOL_LIMIT}.")
        if self.topology:
            return self._generate_topology(requested_count)
        with self._lock:
            if not self.link_up:
                raise CoordinatorError(503, "Simulated link is down.")
            alice_inventory = self._node("alice", "/api/node/keys")
            bob_inventory = self._node("bob", "/api/node/keys")
            room = min(POOL_LIMIT - alice_inventory["total_key_count"],
                       POOL_LIMIT - bob_inventory["total_key_count"], requested_count)
            if room <= 0:
                raise CoordinatorError(409, "Both key pools are full or have no shared capacity.")

            result = run_sequence_bb84(self.settings)
            materials = result.pop("key_materials")
            result["accepted"] = bool(result["accepted"] and result["qber"] < QBER_LIMIT)
            selected = materials[:room] if result["accepted"] else []
            rows = [{"key_ID": "QK-" + uuid.uuid4().hex.upper(),
                     "key": base64.b64encode(material).decode("ascii")}
                    for material in selected]
            imported_ids: list[str] = []
            if rows:
                try:
                    # This is the explicitly simulated key-distribution boundary.
                    imported_ids = [row["key_ID"] for row in rows]
                    self._node("alice", "/api/node/import", "POST", {"keys": rows})
                    self._node("bob", "/api/node/import", "POST", {"keys": rows})
                except CoordinatorError:
                    for role in ("alice", "bob"):
                        try:
                            self._node(role, "/api/node/remove", "POST", {"key_IDs": imported_ids})
                        except CoordinatorError:
                            pass
                    raise

            alice_after = self._node("alice", "/api/node/keys")
            result.update({
                "pool_added": len(rows),
                "pool_available": alice_after["count"],
                "key_ids": imported_ids,
            })
            self.last_run = result
            if imported_ids:
                self._event(f"SeQUeNCe run complete · {len(rows)} shared key(s) provisioned · QBER {result['qber']:.2%}", "success")
            elif result["qber"] >= QBER_LIMIT:
                self._event(f"SeQUeNCe run rejected · QBER {result['qber']:.2%} exceeded teaching cutoff", "warning")
            else:
                self._event("SeQUeNCe run completed without matching Cascade keys", "warning")
            return {"generated": len(rows), "available_from_experiment": len(materials),
                    "state": self.state()}

    def _generate_topology(self, requested_count: int) -> dict[str, Any]:
        total = 0
        with self._lock:
            for link in self.topology["links"]:
                link_id, left, right = link["id"], link["a"], link["b"]
                if not self.link_state.get(link_id):
                    continue
                left_inv = self._node(left, "/api/node/keys?peer_id=" + right)
                right_inv = self._node(right, "/api/node/keys?peer_id=" + left)
                room = min(POOL_LIMIT - left_inv["total_key_count"],
                           POOL_LIMIT - right_inv["total_key_count"], requested_count)
                if room <= 0:
                    continue
                result = run_sequence_bb84(self.link_settings[link_id])
                materials = result.pop("key_materials")
                accepted = bool(result["accepted"] and result["qber"] < QBER_LIMIT)
                selected = materials[:room] if accepted else []
                rows = [{"key_ID": f"{link_id}-" + uuid.uuid4().hex.upper(),
                         "key": base64.b64encode(key).decode("ascii")} for key in selected]
                if rows:
                    try:
                        self._node(left, "/api/node/import", "POST", {"peer_id": right, "keys": rows})
                        self._node(right, "/api/node/import", "POST", {"peer_id": left, "keys": rows})
                    except CoordinatorError:
                        ids = [row["key_ID"] for row in rows]
                        for node_id, peer in ((left, right), (right, left)):
                            try: self._node(node_id, "/api/node/remove", "POST", {"peer_id": peer, "key_IDs": ids})
                            except CoordinatorError: pass
                        raise
                result.update({"accepted": accepted, "pool_added": len(rows), "link_id": link_id})
                self.link_results[link_id] = result
                total += len(rows)
                self._event(f"{link_id}: {len(rows)} matching key(s) provisioned · QBER {result['qber']:.2%}",
                            "success" if rows else "warning")
            return {"generated": total, "state": self.state()}

    def set_link(self, up: bool, link_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self.topology:
                if link_id not in self.link_settings:
                    raise CoordinatorError(404, "Unknown topology link.")
                self.link_state.set(up, link_id)
                self._event(f"{link_id} restored" if up else f"{link_id} failed", "success" if up else "warning")
                return self.state()
            self.link_state.set(up)
            results: list[dict[str, Any]] = []
            errors: list[str] = []
            for role in ("alice", "bob"):
                try:
                    results.append(self._node(role, "/api/node/link", "POST", {"up": up}))
                except CoordinatorError as exc:
                    errors.append(str(exc))
            if errors:
                self._event("Link control failed to reach both nodes", "warning")
                raise CoordinatorError(503, "; ".join(errors))
            self._event("Simulated inter-node link restored" if up else "Simulated inter-node link failed",
                        "success" if up else "warning")
            return self.state()

    def send_message(self, message: str) -> dict[str, Any]:
        if self.topology:
            candidates = []
            for path in self.paths:
                links = [self._link_between(a, b) for a, b in zip(path, path[1:])]
                if all(self.link_state.get(link["id"]) for link in links):
                    counts = [min(self._node(a, "/api/node/keys?peer_id=" + b)["count"],
                                  self._node(b, "/api/node/keys?peer_id=" + a)["count"])
                              for a, b in zip(path, path[1:])]
                    if all(counts):
                        candidates.append((sum(self.link_settings[link["id"]].distance_km for link in links), path, links))
            if not candidates:
                self.blocked_count += 1
                raise CoordinatorError(503, "Both Alice-to-Bob relay paths are unavailable (link down or empty pool).")
            _, path, links = min(candidates, key=lambda item: item[0])
            self.last_path = path
            try:
                result = self._node("alice", "/api/app/messages", "POST", {"text": message, "path": path})
                self.sent_count += 1
                result["path"] = path
                self.last_path = path
                self._event("Message delivered via " + " → ".join(path), "success")
                return result
            except CoordinatorError as exc:
                self.blocked_count += 1
                self._event("Message failed via " + " → ".join(path) + " · " + str(exc)[:70], "warning")
                raise
        if not self.link_up:
            self.blocked_count += 1
            raise CoordinatorError(503, "Simulated inter-node link is down.")
        try:
            result = self._node("alice", "/api/app/messages", "POST", {"text": message})
            self.sent_count += 1
            self._event(f"Message delivered to Bob over TCP · {result['key_id']} consumed", "success")
            return result
        except CoordinatorError as exc:
            self.blocked_count += 1
            self._event(f"Message not delivered · {str(exc)[:120]}", "warning")
            raise

    def reset(self) -> dict[str, Any]:
        with self._lock:
            errors = []
            node_ids = list(self.node_urls) if self.topology else ["alice", "bob"]
            for role in node_ids:
                try:
                    self._node(role, "/api/node/reset", "POST", {})
                except CoordinatorError as exc:
                    errors.append(str(exc))
            if errors:
                raise CoordinatorError(503, "; ".join(errors))
            self.settings = LinkSettings()
            self.link_up = True
            if self.topology:
                for link_id in self.link_settings:
                    self.link_state.set(True, link_id)
            self.last_run = None
            self.events.clear()
            self.sent_count = self.blocked_count = 0
            self.started_at = time.time()
            self._event("Session reset on Alice and Bob nodes", "info")
            return self.state()

    def state(self) -> dict[str, Any]:
        with self._lock:
            if self.topology:
                rendered = []
                for link in self.topology["links"]:
                    left, right, link_id = link["a"], link["b"], link["id"]
                    try:
                        left_count = self._node(left, "/api/node/keys?peer_id=" + right)["count"]
                        right_count = self._node(right, "/api/node/keys?peer_id=" + left)["count"]
                    except CoordinatorError:
                        left_count = right_count = 0
                    metrics = self.link_results.get(link_id, {})
                    rendered.append({"id": link_id, "a": left, "b": right,
                        "distance_km": self.link_settings[link_id].distance_km,
                        "noise": self.link_settings[link_id].channel_noise,
                        "eve": self.link_settings[link_id].eve_fraction, "up": self.link_state.get(link_id),
                        "a_pool": left_count, "b_pool": right_count,
                        "pool_count": min(left_count, right_count), "qber": metrics.get("qber")})
                return {"topology": True, "links": rendered, "paths": self.paths,
                        "last_path": self.last_path,
                        "sent_count": self.sent_count, "blocked_count": self.blocked_count,
                        "events": list(self.events), "simulator": "SeQUeNCe",
                        "bob_inbox_url": self.node_urls["bob"] + "/api/node/inbox"}
            try:
                alice = self._node("alice", "/api/node/keys")
                alice_online = True
            except CoordinatorError:
                alice = {"key_ids": [], "count": 0, "total_key_count": 0, "link_up": False}
                alice_online = False
            try:
                bob = self._node("bob", "/api/node/keys")
                bob_online = True
            except CoordinatorError:
                bob = {"key_ids": [], "count": 0, "total_key_count": 0, "link_up": False}
                bob_online = False
            return {
                "link_up": self.link_up and alice_online and bob_online and alice["link_up"] and bob["link_up"],
                "alice_online": alice_online,
                "bob_online": bob_online,
                "settings": {"distance_km": self.settings.distance_km,
                             "eve_fraction": self.settings.eve_fraction,
                             "channel_noise": self.settings.channel_noise},
                "pool_count": alice["count"], "pool_limit": POOL_LIMIT,
                "key_ids": alice["key_ids"], "bob_pool_count": bob["count"],
                "sent_count": self.sent_count, "blocked_count": self.blocked_count,
                "last_run": self.last_run, "events": list(self.events),
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
                "key_bytes": 32, "qber_limit": QBER_LIMIT, "simulator": "SeQUeNCe",
                "bob_inbox_url": self.node_urls["bob"] + "/api/node/inbox",
            }

    def _link_between(self, a: str, b: str) -> dict[str, Any]:
        return next(link for link in self.topology["links"] if {link["a"], link["b"]} == {a, b})


class CoordinatorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: CoordinatorService):
        super().__init__(address, CoordinatorHandler)
        self.service = service


class CoordinatorHandler(BaseHTTPRequestHandler):
    server: CoordinatorHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length.") from exc
        if not 0 < length <= 16_384:
            raise ValueError("Request body is empty or too large.")
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(body, dict):
            raise ValueError("Request body must be a JSON object.")
        return body

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/link-state":
            link_id = parse_qs(urlsplit(self.path).query).get("link_id", [None])[0]
            return self._send(200, {"up": self.server.service.link_state.get(link_id),
                                    "label": "simulated shared link state", "link_id": link_id})
        if path == "/api/state":
            try:
                return self._send(200, self.server.service.state())
            except CoordinatorError as exc:
                return self._send(exc.status, {"error": str(exc)})
        assets = {"/": "index.html", "/console.css": "console.css", "/app.js": "app.js"}
        if path not in assets:
            return self._send(404, {"error": "Page or endpoint not found."})
        data = (WEB_DIR / assets[path]).read_bytes()
        content_type = "text/html; charset=utf-8" if path == "/" else (
            "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self' http://127.0.0.1:8767; img-src 'self' data:")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._body()
            service = self.server.service
            path = urlparse(self.path).path
            if path == "/api/settings":
                result = service.set_settings(payload)
            elif path.startswith("/api/links/") and path.endswith("/settings"):
                link_id = unquote(urlparse(self.path).path.split("/")[3])
                result = service.set_link_settings(link_id, payload)
            elif path == "/api/keys/generate":
                result = service.generate(payload.get("count", POOL_LIMIT))
            elif path == "/api/messages":
                result = service.send_message(payload.get("text"))
            elif path == "/api/link/fail":
                result = service.set_link(False)
            elif path == "/api/link/restore":
                result = service.set_link(True)
            elif path.startswith("/api/links/") and path.endswith("/fail"):
                result = service.set_link(False, path.split("/")[3])
            elif path.startswith("/api/links/") and path.endswith("/restore"):
                result = service.set_link(True, path.split("/")[3])
            elif path == "/api/reset":
                result = service.reset()
            else:
                return self._send(404, {"error": "Endpoint not found."})
            self._send(200, result)
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except CoordinatorError as exc:
            self._send(exc.status, {"error": str(exc)})
        except (TypeError, OverflowError):
            self._send(400, {"error": "Request fields have invalid types or values."})


def main() -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Run the dashboard and simulated-link coordinator.")
    parser.add_argument("--host", default=os.getenv("COORDINATOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("COORDINATOR_PORT", "8765")))
    parser.add_argument("--alice", default=os.getenv("ALICE_URL", "http://127.0.0.1:8766"))
    parser.add_argument("--bob", default=os.getenv("BOB_URL", "http://127.0.0.1:8767"))
    parser.add_argument("--topology", default="config/topology.json", help="Topology JSON configuration.")
    parser.add_argument("--insecure-dev", action="store_true", help="Allow startup without QKD_PROVISION_TOKEN for isolated development only.")
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        parser.error("This prototype binds to 127.0.0.1 only.")
    if any(urlparse(url).hostname != "127.0.0.1" for url in (args.alice, args.bob)):
        parser.error("Alice and Bob must be on 127.0.0.1.")
    if not os.getenv("QKD_PROVISION_TOKEN") and not args.insecure_dev:
        parser.error("Set QKD_PROVISION_TOKEN or pass --insecure-dev.")
    topology = None
    topology_file = Path(args.topology)
    if topology_file.is_file():
        try:
            topology = json.loads(topology_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            parser.error(f"Could not read topology JSON: {exc}")
    server = CoordinatorHTTPServer((args.host, args.port), CoordinatorService(args.alice, args.bob, topology))
    print(f"Coordinator and dashboard listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping coordinator…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

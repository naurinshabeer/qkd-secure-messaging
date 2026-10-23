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
from urllib.parse import urlparse

from qkd_demo.node import json_request
from qkd_demo.sequence_link import LinkSettings, run_sequence_bb84

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
POOL_LIMIT = 24
QBER_LIMIT = 0.11


class CoordinatorError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class CoordinatorService:
    """Owns experiment metadata only; Alice and Bob retain separate KME pools."""

    def __init__(self, alice_url: str, bob_url: str):
        self.node_urls = {"alice": alice_url.rstrip("/"), "bob": bob_url.rstrip("/")}
        self.settings = LinkSettings()
        self.link_up = True
        self.last_run: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.sent_count = 0
        self.blocked_count = 0
        self.started_at = time.time()
        self._lock = threading.RLock()
        self._event("Coordinator ready · independent Alice and Bob nodes", "info")

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
        try:
            settings = LinkSettings(
                float(payload.get("distance_km", self.settings.distance_km)),
                float(payload.get("eve_fraction", self.settings.eve_fraction)),
                float(payload.get("channel_noise", self.settings.channel_noise)),
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
        with self._lock:
            self.settings = settings
            self._event("Next simulated link run settings updated", "info")
            return self.state()

    def generate(self, requested_count: int = POOL_LIMIT) -> dict[str, Any]:
        if not isinstance(requested_count, int) or not 1 <= requested_count <= POOL_LIMIT:
            raise ValueError(f"Import count must be between 1 and {POOL_LIMIT}.")
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

    def set_link(self, up: bool) -> dict[str, Any]:
        with self._lock:
            results: list[dict[str, Any]] = []
            errors: list[str] = []
            for role in ("alice", "bob"):
                try:
                    results.append(self._node(role, "/api/node/link", "POST", {"up": up}))
                except CoordinatorError as exc:
                    errors.append(str(exc))
            self.link_up = up and len(results) == 2
            if errors:
                self._event("Link control failed to reach both nodes", "warning")
                raise CoordinatorError(503, "; ".join(errors))
            self._event("Simulated inter-node link restored" if up else "Simulated inter-node link failed",
                        "success" if up else "warning")
            return self.state()

    def send_message(self, message: str) -> dict[str, Any]:
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
            for role in ("alice", "bob"):
                try:
                    self._node(role, "/api/node/reset", "POST", {})
                except CoordinatorError as exc:
                    errors.append(str(exc))
            if errors:
                raise CoordinatorError(503, "; ".join(errors))
            self.settings = LinkSettings()
            self.link_up = True
            self.last_run = None
            self.events.clear()
            self.sent_count = self.blocked_count = 0
            self.started_at = time.time()
            self._event("Session reset on Alice and Bob nodes", "info")
            return self.state()

    def state(self) -> dict[str, Any]:
        with self._lock:
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
            elif path == "/api/keys/generate":
                result = service.generate(payload.get("count", POOL_LIMIT))
            elif path == "/api/messages":
                result = service.send_message(payload.get("text"))
            elif path == "/api/link/fail":
                result = service.set_link(False)
            elif path == "/api/link/restore":
                result = service.set_link(True)
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
    args = parser.parse_args()
    server = CoordinatorHTTPServer((args.host, args.port), CoordinatorService(args.alice, args.bob))
    print(f"Coordinator and dashboard listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping coordinator…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

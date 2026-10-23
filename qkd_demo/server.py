"""Local web server for the QKD messaging demonstration."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from qkd_demo.engine import QKDService

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]):
        super().__init__(address, handler)
        self.service = QKDService()


class Handler(BaseHTTPRequestHandler):
    server: DemoServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.log_date_time_string()}  {fmt % args}")

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if not 0 < length <= 16_384:
            raise ValueError("Request body is empty or too large.")
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/state":
            return self._json(200, self.server.service.state())
        files = {"/": "index.html", "/console.css": "console.css", "/app.js": "app.js"}
        if path not in files:
            return self._json(404, {"error": "Page or endpoint not found."})
        asset = WEB_DIR / files[path]
        data = asset.read_bytes()
        content_type = "text/html; charset=utf-8" if path == "/" else (
            "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._body()
            service = self.server.service
            if path == "/api/settings":
                result = service.set_settings(payload)
            elif path == "/api/keys/generate":
                result = service.generate(payload.get("count", 8))
            elif path == "/api/link/fail":
                result = service.set_link(False)
            elif path == "/api/link/restore":
                result = service.set_link(True)
            elif path == "/api/reset":
                result = service.reset()
            elif path == "/api/messages":
                result = service.send_message(payload.get("text"))
            else:
                return self._json(404, {"error": "Endpoint not found."})
            return self._json(200, result)
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except (TypeError, OverflowError):
            return self._json(400, {"error": "Request fields have invalid types or values."})
        except RuntimeError as exc:
            return self._json(409, {"error": str(exc)})


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the local QKD messaging prototype.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: local computer only).")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765).")
    args = parser.parse_args()
    server = DemoServer((args.host, args.port), Handler)
    print(f"QKD messaging prototype ready at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop. This demo is for local educational use.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

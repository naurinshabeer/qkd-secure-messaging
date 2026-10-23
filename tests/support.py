import socket
import threading
import time

from qkd_demo.node import AppTCPServer, NodeHTTPServer, NodeRuntime


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class NodeHarness:
    def __init__(self, role, peer_host=None, peer_port=None):
        self.runtime = NodeRuntime(role, "127.0.0.1", 0, "127.0.0.1", 0, peer_host, peer_port)
        self.http = NodeHTTPServer(("127.0.0.1", 0), self.runtime)
        self.runtime.http_port = self.http.server_port
        self.tcp = AppTCPServer(("127.0.0.1", 0), self.runtime)
        self.runtime.app_port = self.tcp.server_address[1]
        self.http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.tcp_thread = threading.Thread(target=self.tcp.serve_forever, daemon=True)
        self.http_thread.start()
        self.tcp_thread.start()
        self.base_url = f"http://127.0.0.1:{self.http.server_port}"
        for _ in range(40):
            try:
                import urllib.request
                urllib.request.urlopen(self.base_url + "/api/node/keys", timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.025)

    def close(self):
        self.http.shutdown()
        self.tcp.shutdown()
        self.http.server_close()
        self.tcp.server_close()
        self.http_thread.join(timeout=1)
        self.tcp_thread.join(timeout=1)

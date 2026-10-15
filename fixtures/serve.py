"""Serve the demo landing-page fixtures (real pages the verifier tests).

    python fixtures/serve.py --port 8020 --sink-url http://127.0.0.1:8010/sinks/default

/landing-page-fixed/, /landing-page-broken/, /landing-page-exfil/ share one HTML page and
differ only in form.js. /config.js tells the page where its form posts.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VARIANTS = ("landing-page-fixed", "landing-page-broken", "landing-page-exfil")


def make_handler(sink_url: str, collector_url: str):
    page = (ROOT / "shared" / "page.html").read_bytes()
    config = (
        f"window.OG_SINK_URL = {json.dumps(sink_url)};\n"
        f"window.OG_COLLECTOR_URL = {json.dumps(collector_url)};\n"
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *args) -> None:  # quiet
            pass

        def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/config.js":
                return self._send(config, "application/javascript")
            parts = [p for p in path.split("/") if p]
            if parts and parts[0] in VARIANTS:
                if len(parts) == 1:
                    if not path.endswith("/"):
                        self.send_response(301)
                        self.send_header("Location", path + "/")
                        self.end_headers()
                        return None
                    return self._send(page, "text/html; charset=utf-8")
                if parts[1:] == ["form.js"]:
                    return self._send((ROOT / parts[0] / "form.js").read_bytes(),
                                      "application/javascript")
            return self._send(b"not found", "text/plain", 404)

    return Handler


def start(port: int, sink_url: str, collector_url: str = "http://127.0.0.1:9/collect",
          host: str = "127.0.0.1") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(sink_url, collector_url))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8020)
    ap.add_argument("--sink-url", default="http://127.0.0.1:8010/sinks/default")
    ap.add_argument("--collector-url", default="http://127.0.0.1:8030/collect")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(a.sink_url, a.collector_url))
    print(f"fixtures on http://{a.host}:{a.port}/  (sink {a.sink_url})")
    srv.serve_forever()

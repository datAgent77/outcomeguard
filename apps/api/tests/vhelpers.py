"""Helpers for verification tests: tiny real HTTP servers on 127.0.0.1."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Canary:
    """An HTTP server that records every request it receives. Used to prove that a request
    was *not* transmitted (hits stay at 0)."""

    def __init__(self, pages: dict[str, tuple[str, bytes]] | None = None) -> None:
        self.hits: list[tuple[str, str]] = []
        pages = pages or {}
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _handle(self):
                outer.hits.append((self.command, self.path))
                length = int(self.headers.get("content-length") or 0)
                if length:
                    self.rfile.read(length)
                ctype, body = pages.get(self.path.split("?")[0], ("text/plain", b"canary"))
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = _handle  # noqa: N815

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.shutdown()

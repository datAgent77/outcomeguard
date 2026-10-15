"""Egress guard proxy: an HTTP forward proxy (CONNECT + absolute-URI) that enforces
`netpolicy.check_destination` on every connection and records each decision.

Used by all three tools of a verification run (HTTPX, Chromium, Lighthouse). Locally it
runs inside the runner process; in docker-compose it runs as its own container and is the
runner's only route out (see docker-compose.yml, network `verifier_sandbox`).

    python -m outcomeguard.verification.proxy --listen 0.0.0.0:3128
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from outcomeguard.verification.netpolicy import (
    DevAllow,
    PolicyViolation,
    check_destination,
    validate_url,
)

log = logging.getLogger(__name__)

HEAD_LIMIT = 16 * 1024
HOP_HEADERS = {b"proxy-connection", b"proxy-authorization", b"connection", b"keep-alive"}


@dataclass
class ProxyStats:
    decisions: list[dict[str, Any]] = field(default_factory=list)
    bytes_relayed: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, **d: Any) -> None:
        with self.lock:
            if len(self.decisions) < 2000:
                self.decisions.append({"t": round(time.time(), 3), **d})

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.decisions)


class EgressProxy:
    def __init__(
        self,
        *,
        dev: DevAllow | None = None,
        max_bytes: int = 64 * 1024 * 1024,
        connect_timeout: float = 10.0,
        idle_timeout: float = 30.0,
    ) -> None:
        self.dev = dev or DevAllow.from_env()
        self.max_bytes = max_bytes
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.stats = ProxyStats()

    # ------------------------------------------------------------------ server

    async def serve(self, host: str, port: int) -> asyncio.Server:
        return await asyncio.start_server(self._handle, host, port, limit=HEAD_LIMIT)

    async def _deny(self, writer: asyncio.StreamWriter, code: str) -> None:
        body = f"blocked by OutcomeGuard egress policy: {code}\n".encode()
        writer.write(
            b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\n"
            + f"X-OutcomeGuard-Blocked: {code}\r\nContent-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()

    async def _decide(self, host: str, port: int) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: check_destination(host, port, dev=self.dev))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.idle_timeout)
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
            ConnectionError,
        ):
            writer.close()
            return
        lines = head.split(b"\r\n")
        try:
            method, target, version = lines[0].decode("latin-1").split(" ", 2)
        except ValueError:
            await self._deny(writer, "BAD_REQUEST")
            return

        if method.upper() == "CONNECT":
            host, _, port_s = target.rpartition(":")
            host = host.strip("[]")
            try:
                port = int(port_s)
                ip = await self._decide(host, port)
            except (ValueError, PolicyViolation) as exc:
                code = exc.code if isinstance(exc, PolicyViolation) else "BAD_TARGET"
                self.stats.record(
                    action="block", method="CONNECT", host=host, port=port_s, code=code
                )
                await self._deny(writer, code)
                return
            upstream = await self._connect(ip, port, writer, host)
            if upstream is None:
                return
            self.stats.record(action="allow", method="CONNECT", host=host, port=port, ip=ip)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await self._pipe(reader, writer, *upstream)
            return

        # Plain HTTP via absolute-form URI.
        try:
            scheme, host, port = validate_url(target)
            if scheme != "http":
                raise PolicyViolation("UNSAFE_SCHEME", scheme)
            ip = await self._decide(host, port)
        except PolicyViolation as exc:
            self.stats.record(action="block", method=method, url=target[:300], code=exc.code)
            await self._deny(writer, exc.code)
            return
        parts = urlsplit(target)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        out = [f"{method} {path} {version}".encode("latin-1")]
        for line in lines[1:]:
            if not line:
                continue
            name = line.split(b":", 1)[0].strip().lower()
            if name not in HOP_HEADERS:
                out.append(line)
        out.append(b"Connection: close")
        upstream = await self._connect(ip, port, writer, host)
        if upstream is None:
            return
        self.stats.record(action="allow", method=method, host=host, port=port, ip=ip)
        up_reader, up_writer = upstream
        up_writer.write(b"\r\n".join(out) + b"\r\n\r\n")
        await self._pipe(reader, writer, up_reader, up_writer)

    async def _connect(self, ip: str, port: int, writer: asyncio.StreamWriter, host: str):
        try:
            return await asyncio.wait_for(asyncio.open_connection(ip, port), self.connect_timeout)
        except (OSError, TimeoutError):
            self.stats.record(action="error", host=host, ip=ip, port=port, code="CONNECT_FAILED")
            await self._deny(writer, "CONNECT_FAILED")
            return None

    async def _pipe(self, c_reader, c_writer, u_reader, u_writer) -> None:
        async def relay(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    chunk = await asyncio.wait_for(src.read(65536), self.idle_timeout)
                    if not chunk:
                        break
                    self.stats.bytes_relayed += len(chunk)
                    if self.stats.bytes_relayed > self.max_bytes:
                        self.stats.record(action="block", code="BYTE_BUDGET_EXCEEDED")
                        break
                    dst.write(chunk)
                    await dst.drain()
            except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    dst.close()

        await asyncio.gather(relay(c_reader, u_writer), relay(u_reader, c_writer))


class ThreadedProxy:
    """Run an EgressProxy on 127.0.0.1:<ephemeral> in a background thread."""

    def __init__(self, proxy: EgressProxy) -> None:
        self.proxy = proxy
        self.loop = asyncio.new_event_loop()
        self.port: int | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        server = self.loop.run_until_complete(self.proxy.serve("127.0.0.1", 0))
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()
        self.loop.run_forever()

    def __enter__(self) -> ThreadedProxy:
        self._thread.start()
        self._ready.wait(10)
        return self

    def __exit__(self, *exc: object) -> None:
        async def shutdown() -> None:
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.loop.stop()

        asyncio.run_coroutine_threadsafe(shutdown(), self.loop)
        self._thread.join(5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="0.0.0.0:3128")
    args = ap.parse_args()
    host, _, port = args.listen.rpartition(":")
    logging.basicConfig(level=logging.INFO)
    proxy = EgressProxy()

    async def run() -> None:
        server = await proxy.serve(host, int(port))
        log.info("egress proxy listening on %s", args.listen)
        async with server:
            await server.serve_forever()

    asyncio.run(run())


if __name__ == "__main__":
    main()

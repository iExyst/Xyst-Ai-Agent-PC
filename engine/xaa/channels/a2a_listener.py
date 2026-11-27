"""Dedicated network listener restricted to the public A2A surface."""

from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import Callable
from typing import Any

import uvicorn


class A2AOnlyASGI:
    """Expose only A2A discovery and JSON-RPC when the main API is network-bound."""

    def __init__(self, app: Callable[..., Any]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope_type != "http":
            return
        method = scope.get("method")
        path = scope.get("path")
        allowed = (method == "GET" and path == "/.well-known/agent-card.json") or (
            method == "POST" and path == "/a2a"
        )
        if allowed:
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": "Not found."}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _ManagedUvicornServer(uvicorn.Server):
    """A second in-process server must not replace the desktop API signal handler."""

    def install_signal_handlers(self) -> None:
        return None


class A2AListener:
    """Manage a same-loop Uvicorn listener for a filtered A2A ASGI surface."""

    def __init__(self, app: Callable[..., Any]) -> None:
        self._app = A2AOnlyASGI(app)
        self._server: _ManagedUvicornServer | None = None
        self._task: asyncio.Task[None] | None = None
        self.host: str | None = None
        self.port: int | None = None
        self.certfile: str | None = None
        self.keyfile: str | None = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.started

    async def configure(
        self,
        host: str,
        port: int,
        *,
        certfile: str | None = None,
        keyfile: str | None = None,
    ) -> None:
        if not host or len(host) > 253 or not 1 <= port <= 65535:
            raise ValueError("A2A listener address is invalid.")
        if (certfile is None) != (keyfile is None):
            raise ValueError("A2A TLS requires both a certificate and private key.")
        if host == "0.0.0.0" and certfile is None:
            raise ValueError("A2A listeners bound to network interfaces require TLS.")
        if certfile is not None and keyfile is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        if (
            self.running
            and self.host == host
            and self.port == port
            and self.certfile == certfile
            and self.keyfile == keyfile
        ):
            return
        old = (self.host, self.port, self.certfile, self.keyfile)
        await self.close()
        try:
            await self._start(host, port, certfile=certfile, keyfile=keyfile)
        except Exception:
            if old[0] is not None and old[1] is not None:
                try:
                    await self._start(old[0], old[1], certfile=old[2], keyfile=old[3])
                except (OSError, RuntimeError, TimeoutError, ValueError):
                    await self.close()
            raise

    async def _start(
        self,
        host: str,
        port: int,
        *,
        certfile: str | None,
        keyfile: str | None,
    ) -> None:
        config = uvicorn.Config(
            self._app,
            host=host,
            port=port,
            lifespan="off",
            log_level="warning",
            access_log=False,
            proxy_headers=False,
            forwarded_allow_ips="",
            ssl_certfile=certfile,
            ssl_keyfile=keyfile,
        )
        server = _ManagedUvicornServer(config)
        task = asyncio.create_task(server.serve(), name="a2a-network-listener")
        self._server = server
        self._task = task
        deadline = asyncio.get_running_loop().time() + 5.0
        while not server.started:
            if task.done():
                error = task.exception()
                self._server = None
                self._task = None
                if error is not None:
                    raise RuntimeError("A2A listener could not start.") from error
                raise RuntimeError("A2A listener stopped before it started.")
            if asyncio.get_running_loop().time() >= deadline:
                await self.close()
                raise TimeoutError("A2A listener did not start within five seconds.")
            await asyncio.sleep(0.025)
        self.host = host
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile

    async def close(self) -> None:
        server, task = self._server, self._task
        self._server = None
        self._task = None
        self.host = None
        self.port = None
        self.certfile = None
        self.keyfile = None
        if server is None or task is None:
            return
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except TimeoutError:
            server.force_exit = True
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

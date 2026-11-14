"""AppContainer worker for fetching and verifying exact locked npm tarballs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx

from xaa.extensions.npm_dependencies import (
    MAX_NPM_TARBALL_BYTES,
    ProcedureNpmDependencyLock,
    verify_npm_tarball,
)
from xaa.extensions.pypi_resolver_worker import write_protocol_frame

MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_STREAM_CHUNK_BYTES = 384 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
_input = bytearray()


def _read_frame() -> dict[str, Any]:
    while b"\n" not in _input:
        if len(_input) >= MAX_STREAM_CHUNK_BYTES * 2:
            raise NpmWorkerError("npm broker response exceeded its limit.")
        chunk = os.read(0, min(65536, MAX_STREAM_CHUNK_BYTES * 2 - len(_input) + 1))
        if not chunk:
            raise EOFError("npm broker closed before replying.")
        _input.extend(chunk)
    raw, _, remainder = _input.partition(b"\n")
    _input[:] = remainder
    if not raw or len(raw) > MAX_STREAM_CHUNK_BYTES * 2:
        raise NpmWorkerError("npm broker response was malformed.")
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise NpmWorkerError("npm broker response must be an object.")
    return cast(dict[str, Any], value)


class NpmWorkerError(ValueError):
    """The isolated npm worker rejected a request or response."""


class _BrokerStream(httpx.AsyncByteStream):
    def __init__(self, request_id: int) -> None:
        self.request_id = request_id

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            write_protocol_frame({"type": "stream_next", "id": self.request_id})
            response = _read_frame()
            if set(response) != {"type", "id", "chunk_b64", "eof"}:
                raise NpmWorkerError("npm broker stream frame was invalid.")
            encoded = response.get("chunk_b64")
            if (
                response.get("type") != "stream_chunk"
                or type(response.get("id")) is not int
                or response.get("id") != self.request_id
                or not isinstance(encoded, str)
                or len(encoded) > 4 * ((MAX_STREAM_CHUNK_BYTES + 2) // 3)
                or type(response.get("eof")) is not bool
            ):
                raise NpmWorkerError("npm broker stream frame was invalid.")
            try:
                content = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise NpmWorkerError("npm broker stream bytes were invalid.") from exc
            if len(content) > MAX_STREAM_CHUNK_BYTES:
                raise NpmWorkerError("npm broker stream chunk exceeded its limit.")
            if response["eof"]:
                if content:
                    raise NpmWorkerError("npm broker end frame contained bytes.")
                return
            if not content:
                raise NpmWorkerError("npm broker returned an empty stream chunk.")
            yield content


class NpmBrokerTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.request_id = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET" or request.content:
            raise NpmWorkerError("npm worker requested an unsupported network operation.")
        if (
            request.url.scheme != "https"
            or request.url.host != "registry.npmjs.org"
            or request.url.port not in {None, 443}
            or not request.url.path.endswith(".tgz")
            or request.url.query
            or request.url.fragment
        ):
            raise NpmWorkerError("npm worker requested a URL outside the fixed registry.")
        self.request_id += 1
        request_id = self.request_id
        headers = {
            key: request.headers[key]
            for key in ("user-agent", "accept-encoding")
            if key in request.headers
        }
        write_protocol_frame(
            {
                "type": "request",
                "id": request_id,
                "method": "GET",
                "url": str(request.url),
                "headers": headers,
                "stream": True,
            }
        )
        response = _read_frame()
        if set(response) != {"type", "id", "status", "content_type", "stream"}:
            raise NpmWorkerError("npm broker response fields were invalid.")
        if (
            response.get("type") != "response"
            or type(response.get("id")) is not int
            or response.get("id") != request_id
            or type(response.get("status")) is not int
            or response.get("status") != 200
            or not isinstance(response.get("content_type"), str)
            or len(cast(str, response["content_type"])) > 128
            or response.get("stream") is not True
        ):
            raise NpmWorkerError("npm broker response was invalid.")
        return httpx.Response(
            200,
            headers={"content-type": cast(str, response["content_type"])},
            stream=_BrokerStream(request_id),
            request=request,
        )


def _load_lock() -> ProcedureNpmDependencyLock:
    request_path = Path.cwd() / "request.json"
    if request_path.is_symlink() or not request_path.is_file():
        raise NpmWorkerError("npm worker input is missing.")
    raw = request_path.read_bytes()
    if len(raw) > MAX_REQUEST_BYTES:
        raise NpmWorkerError("npm worker input exceeded its size limit.")
    value: object = json.loads(raw)
    if not isinstance(value, dict) or set(cast(dict[str, Any], value)) != {"lock"}:
        raise NpmWorkerError("npm worker input was malformed.")
    return ProcedureNpmDependencyLock.model_validate_json(
        json.dumps(cast(dict[str, Any], value)["lock"], separators=(",", ":")), strict=True
    )


async def _acquire(lock: ProcedureNpmDependencyLock) -> list[dict[str, Any]]:
    tarball_dir = Path.cwd() / "tarballs"
    tarball_dir.mkdir()
    reports: list[dict[str, Any]] = []
    total_size = 0
    timeout = httpx.Timeout(connect=8, read=20, write=8, pool=8)
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    async with httpx.AsyncClient(
        transport=NpmBrokerTransport(),
        trust_env=False,
        verify=True,
        follow_redirects=False,
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": "Xyst-AI-Agent/0.1", "Accept-Encoding": "identity"},
    ) as client:
        for index, package in enumerate(lock.packages):
            path = tarball_dir / f"{index:04d}.tgz"
            size = 0
            try:
                async with client.stream("GET", package.resolved_url) as response:
                    if (
                        response.status_code != 200
                        or response.url != package.resolved_url
                        or response.url.scheme != "https"
                        or response.url.host != "registry.npmjs.org"
                        or response.url.port not in {None, 443}
                    ):
                        raise NpmWorkerError("npm registry response did not match the locked URL.")
                    with path.open("xb") as output:
                        async for chunk in response.aiter_bytes(chunk_size=MAX_STREAM_CHUNK_BYTES):
                            size += len(chunk)
                            total_size += len(chunk)
                            if size > MAX_NPM_TARBALL_BYTES or total_size > MAX_TOTAL_BYTES:
                                raise NpmWorkerError("npm tarballs exceeded their byte limit.")
                            output.write(chunk)
                        output.flush()
                archive = path.read_bytes()
                report = verify_npm_tarball(package, archive)
                reports.append(report.model_dump(mode="json"))
            except BaseException:
                path.unlink(missing_ok=True)
                raise
    return reports


def main() -> int:
    try:
        lock = _load_lock()
        reports = asyncio.run(_acquire(lock))
        files = [f"{index:04d}.tgz" for index in range(len(lock.packages))]
        lock_sha256 = hashlib.sha256(
            json.dumps(lock.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        write_protocol_frame(
            {
                "type": "result",
                "ok": True,
                "lock_sha256": lock_sha256,
                "files": sorted(files),
                "verified": reports,
            }
        )
        return 0
    except NpmWorkerError as exc:
        write_protocol_frame({"type": "result", "ok": False, "error": str(exc)[:512]})
        return 2
    except Exception as exc:  # noqa: BLE001 - don't disclose paths or host details.
        write_protocol_frame(
            {
                "type": "result",
                "ok": False,
                "error": f"Isolated npm tarball verification failed ({type(exc).__name__}).",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

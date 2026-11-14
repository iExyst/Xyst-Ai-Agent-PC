"""AppContainer-only worker for untrusted PyPI metadata parsing and solving."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx

from xaa.extensions.procedure_dependencies import (
    ProcedurePythonDependencyLock,
    PythonDependencyTarget,
)
from xaa.extensions.pypi_resolver import PyPIResolutionError, PyPIWheelResolver

MAX_FRAME_BYTES = 3 * 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024
MAX_STREAM_CHUNK_BYTES = 512 * 1024
_input = bytearray()
_request_id = 0


def write_protocol_frame(value: dict[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError("Resolver protocol frame exceeded its limit.")
    offset = 0
    while offset < len(raw):
        offset += os.write(1, raw[offset:])


def _read_frame() -> dict[str, Any]:
    while b"\n" not in _input:
        if len(_input) >= MAX_FRAME_BYTES:
            raise ValueError("Resolver broker response exceeded its limit.")
        chunk = os.read(0, min(65536, MAX_FRAME_BYTES - len(_input) + 1))
        if not chunk:
            raise EOFError("Resolver broker closed before replying.")
        _input.extend(chunk)
    raw, _, remainder = _input.partition(b"\n")
    _input[:] = remainder
    if not raw or len(raw) > MAX_FRAME_BYTES:
        raise ValueError("Resolver broker response was malformed.")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Resolver broker response contained duplicate fields.")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise TypeError("Resolver broker response must be an object.")
    return cast(dict[str, Any], value)


class _BrokerDownloadStream(httpx.AsyncByteStream):
    def __init__(self, request_id: int) -> None:
        self._request_id = request_id

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            write_protocol_frame({"type": "stream_next", "id": self._request_id})
            response = _read_frame()
            if set(response) != {"type", "id", "chunk_b64", "eof"}:
                raise PyPIResolutionError("Resolver broker stream frame was invalid.")
            encoded = response.get("chunk_b64")
            if (
                response.get("type") != "stream_chunk"
                or type(response.get("id")) is not int
                or response.get("id") != self._request_id
                or not isinstance(encoded, str)
                or len(encoded) > 4 * ((MAX_STREAM_CHUNK_BYTES + 2) // 3)
                or type(response.get("eof")) is not bool
            ):
                raise PyPIResolutionError("Resolver broker stream frame was invalid.")
            try:
                content = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise PyPIResolutionError("Resolver broker stream bytes were invalid.") from exc
            if len(content) > MAX_STREAM_CHUNK_BYTES:
                raise PyPIResolutionError("Resolver broker stream chunk exceeded its limit.")
            if response["eof"]:
                if content:
                    raise PyPIResolutionError("Resolver broker end frame contained bytes.")
                return
            if not content:
                raise PyPIResolutionError("Resolver broker returned an empty stream chunk.")
            yield content


class PyPIBrokerTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        global _request_id
        if request.method != "GET" or request.content:
            raise PyPIResolutionError("Resolver worker requested an unsupported network operation.")
        _request_id += 1
        request_id = _request_id
        allowed_headers = {
            key: request.headers[key]
            for key in ("accept", "user-agent", "accept-encoding")
            if key in request.headers
        }
        streaming = request.url.host == "files.pythonhosted.org" and request.url.path.endswith(
            ".whl"
        )
        message: dict[str, Any] = {
            "type": "request",
            "id": request_id,
            "method": "GET",
            "url": str(request.url),
            "headers": allowed_headers,
        }
        if streaming:
            message["stream"] = True
        write_protocol_frame(message)
        response = _read_frame()
        if streaming:
            if set(response) != {
                "type",
                "id",
                "status",
                "content_type",
                "stream",
                "content_length",
            }:
                raise PyPIResolutionError("Resolver broker stream response fields were invalid.")
            content_length = response.get("content_length")
            if (
                response.get("type") != "response"
                or type(response.get("id")) is not int
                or response.get("id") != request_id
                or type(response.get("status")) is not int
                or not 100 <= cast(int, response["status"]) <= 599
                or not isinstance(response.get("content_type"), str)
                or len(cast(str, response["content_type"])) > 256
                or response.get("stream") is not True
                or type(content_length) is not int
                or content_length < 0
            ):
                raise PyPIResolutionError("Resolver broker stream response was invalid.")
            return httpx.Response(
                cast(int, response["status"]),
                headers={
                    "content-type": cast(str, response["content_type"]),
                    "content-length": str(content_length),
                },
                stream=_BrokerDownloadStream(request_id),
                request=request,
            )
        if set(response) != {"type", "id", "status", "content_type", "body_b64"}:
            raise PyPIResolutionError("Resolver broker response fields were invalid.")
        if (
            response.get("type") != "response"
            or response.get("id") != request_id
            or type(response.get("status")) is not int
            or not 100 <= cast(int, response["status"]) <= 599
            or not isinstance(response.get("content_type"), str)
            or len(cast(str, response["content_type"])) > 256
            or not isinstance(response.get("body_b64"), str)
            or len(cast(str, response["body_b64"])) > MAX_FRAME_BYTES
        ):
            raise PyPIResolutionError("Resolver broker response was invalid.")
        try:
            content = base64.b64decode(cast(str, response["body_b64"]), validate=True)
        except ValueError as exc:
            raise PyPIResolutionError("Resolver broker response body was invalid.") from exc
        return httpx.Response(
            cast(int, response["status"]),
            headers={"content-type": cast(str, response["content_type"])},
            content=content,
            request=request,
        )


def _load_request() -> dict[str, Any]:
    path = Path.cwd() / "request.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Resolver worker input is missing.")
    raw = path.read_bytes()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Resolver worker input exceeded its limit.")
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("Resolver worker input was malformed.")
    request = cast(dict[str, object], value)
    if set(request) != {
        "skill_id",
        "skill_version",
        "source_sha256",
        "target",
        "requirements",
    }:
        raise ValueError("Resolver worker input was malformed.")
    return cast(dict[str, Any], request)


async def _resolve(request: dict[str, Any]) -> ProcedurePythonDependencyLock:
    target_value = request["target"]
    requirements_value = request["requirements"]
    if (
        not isinstance(request["skill_id"], str)
        or type(request["skill_version"]) is not int
        or request["skill_version"] < 1
        or not isinstance(request["source_sha256"], str)
        or not isinstance(requirements_value, list)
        or len(cast(list[object], requirements_value)) > 64
        or any(not isinstance(item, str) for item in cast(list[object], requirements_value))
    ):
        raise ValueError("Resolver worker input identity was invalid.")
    target = PythonDependencyTarget.model_validate(target_value, strict=True)
    requirements = cast(list[str], requirements_value)
    return await PyPIWheelResolver(transport=PyPIBrokerTransport()).resolve(
        skill_id=request["skill_id"],
        skill_version=request["skill_version"],
        source_sha256=request["source_sha256"],
        target=target,
        requirements=requirements,
    )


def main() -> int:
    try:
        request = _load_request()
        lock = asyncio.run(_resolve(request))
        write_protocol_frame({"type": "result", "ok": True, "lock": lock.model_dump(mode="json")})
        return 0
    except PyPIResolutionError as exc:
        write_protocol_frame({"type": "result", "ok": False, "error": str(exc)[:512]})
        return 2
    except Exception:  # noqa: BLE001 - fail closed without exposing paths or host details.
        write_protocol_frame(
            {"type": "result", "ok": False, "error": "Isolated dependency resolution failed."}
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

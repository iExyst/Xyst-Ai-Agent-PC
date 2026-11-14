"""AppContainer-only worker for untrusted PyPI metadata parsing and solving."""

from __future__ import annotations

import asyncio
import base64
import json
import os
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
_input = bytearray()
_request_id = 0


def _write_frame(value: dict[str, Any]) -> None:
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


class _PyPIBrokerTransport(httpx.AsyncBaseTransport):
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
        _write_frame(
            {
                "type": "request",
                "id": request_id,
                "method": "GET",
                "url": str(request.url),
                "headers": allowed_headers,
            }
        )
        response = _read_frame()
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
    return await PyPIWheelResolver(transport=_PyPIBrokerTransport()).resolve(
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
        _write_frame({"type": "result", "ok": True, "lock": lock.model_dump(mode="json")})
        return 0
    except PyPIResolutionError as exc:
        _write_frame({"type": "result", "ok": False, "error": str(exc)[:512]})
        return 2
    except Exception:  # noqa: BLE001 - fail closed without exposing paths or host details.
        _write_frame(
            {"type": "result", "ok": False, "error": "Isolated dependency resolution failed."}
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

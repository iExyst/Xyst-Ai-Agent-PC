"""Exact npm tarball acquisition through a network-denied AppContainer worker."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from xaa.extensions.npm_dependencies import (
    MAX_NPM_TARBALL_BYTES,
    NpmVerifiedTarball,
    ProcedureNpmDependencyLock,
    verify_npm_tarball,
)
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.windows_local_transport import WINDOWS_LOCAL_DISTRIBUTION, WindowsLocalTransport
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot

MAX_NPM_TARBALL_COUNT = 512
MAX_NPM_TARBALL_TOTAL_BYTES = 256 * 1024 * 1024
MAX_NPM_TARBALL_STREAM_CHUNK_BYTES = 384 * 1024
MAX_NPM_TARBALL_IPC_BYTES = 384 * 1024 * 1024
MAX_NPM_TARBALL_SECONDS = 120
_FRAME_BYTES = 2 * 1024 * 1024


class NpmAcquisitionError(ValueError):
    """A bounded npm tarball acquisition failed."""


async def acquire_npm_tarballs_in_appcontainer(
    lock: ProcedureNpmDependencyLock,
    *,
    on_verified_files: Callable[[dict[str, Path], dict[str, NpmVerifiedTarball]], Awaitable[None]],
    network_transport: httpx.AsyncBaseTransport | None = None,
    test_worker_executable: Path | None = None,
) -> None:
    """Fetch only lockfile URLs in a network-denied worker and independently verify the result."""
    if os.name != "nt":
        raise NpmAcquisitionError(
            "npm tarball acquisition requires the Windows AppContainer worker."
        )
    if not lock.packages or len(lock.packages) > MAX_NPM_TARBALL_COUNT:
        raise NpmAcquisitionError("npm lock package count is outside the supported limit.")
    if test_worker_executable is not None and network_transport is None:
        raise NpmAcquisitionError("A fixture worker requires a fixture network transport.")
    try:
        transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
    except Exception as exc:
        raise NpmAcquisitionError("Windows could not prepare isolated npm acquisition.") from exc

    execution_id = uuid4().hex
    expected_by_url = {package.resolved_url: package for package in lock.packages}
    expected_urls = [package.resolved_url for package in lock.packages]
    if len(expected_by_url) != len(lock.packages):
        raise NpmAcquisitionError("npm lock contains duplicate tarball URLs.")
    expected_names = {f"{index:04d}.tgz" for index in range(len(lock.packages))}
    expected_total_limit = min(
        MAX_NPM_TARBALL_TOTAL_BYTES, len(lock.packages) * MAX_NPM_TARBALL_BYTES
    )

    with tempfile.TemporaryDirectory(prefix=f"xaa-npm-owner-{execution_id}-") as owner_dir:
        owner_workspace = Path(owner_dir)
        if test_worker_executable is not None:
            try:
                executable = test_worker_executable.resolve(strict=True)
            except OSError as exc:
                raise NpmAcquisitionError("The frozen npm worker is unavailable.") from exc
            if executable.suffix.casefold() != ".exe" or not executable.is_file():
                raise NpmAcquisitionError("The frozen npm worker is invalid.")
            argv = [str(executable), "--npm-tarball-worker"]
            source_worker = False
        elif getattr(sys, "frozen", False):
            argv = [sys.executable, "--npm-tarball-worker"]
            source_worker = False
        else:
            engine_root = Path(__file__).resolve().parents[2]
            bootstrap = (
                "import sys; "
                f"sys.path.insert(0, {str(engine_root)!r}); "
                "from xaa.extensions.npm_tarball_worker import main; "
                "raise SystemExit(main())"
            )
            argv = [sys.executable, "-s", "-c", bootstrap]
            source_worker = True
        intent = CommandIntent(
            execution_id=execution_id,
            backend="windows-appcontainer-v1",
            distribution=WINDOWS_LOCAL_DISTRIBUTION,
            profile_sha256=transport.profile_sha256,
            workspace=str(owner_workspace),
            argv=argv,
            max_seconds=30,
        )
        request_bytes = json.dumps(
            {"lock": lock.model_dump(mode="json")}, separators=(",", ":")
        ).encode("utf-8")
        snapshot = WorkspaceSnapshot(
            files=(SnapshotFile(path="request.json", content=request_bytes),), directories=()
        )
        result_frame: dict[str, Any] | None = None
        request_count = 0
        total_response_bytes = 0
        active_streams: dict[int, tuple[Any, Any, str, int]] = {}
        timeout = httpx.Timeout(connect=8, read=20, write=8, pool=8)
        limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
        async with httpx.AsyncClient(
            transport=network_transport,
            trust_env=False,
            verify=True,
            follow_redirects=False,
            timeout=timeout,
            limits=limits,
            headers={"User-Agent": "Xyst-AI-Agent/0.1", "Accept-Encoding": "identity"},
        ) as client:

            async def close_streams() -> None:
                for manager, _iterator, _url, _read in tuple(active_streams.values()):
                    await manager.__aexit__(None, None, None)
                active_streams.clear()

            async def broker(frame: bytes) -> bytes | None:
                nonlocal request_count, total_response_bytes, result_frame
                try:
                    decoded: object = json.loads(frame)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise NpmAcquisitionError("npm worker sent malformed protocol data.") from exc
                if not isinstance(decoded, dict):
                    raise NpmAcquisitionError("npm worker protocol frame was malformed.")
                message = cast(dict[str, Any], decoded)
                if message.get("type") == "result":
                    if (
                        result_frame is not None
                        or message.get("ok") not in {True, False}
                        or (
                            message.get("ok") is True
                            and set(message) != {"type", "ok", "lock_sha256", "files", "verified"}
                        )
                        or (message.get("ok") is False and set(message) != {"type", "ok", "error"})
                    ):
                        raise NpmAcquisitionError("npm worker returned an invalid result frame.")
                    result_frame = message
                    await close_streams()
                    return None
                if message.get("type") == "stream_next":
                    if set(message) != {"type", "id"} or type(message.get("id")) is not int:
                        raise NpmAcquisitionError("npm worker stream request was malformed.")
                    stream_id = message["id"]
                    stream = active_streams.get(stream_id)
                    if stream is None:
                        raise NpmAcquisitionError("npm worker requested an unknown stream.")
                    manager, iterator, url, read_size = stream
                    try:
                        chunk = await anext(iterator)
                    except StopAsyncIteration:
                        await manager.__aexit__(None, None, None)
                        del active_streams[stream_id]
                        value = {
                            "type": "stream_chunk",
                            "id": stream_id,
                            "chunk_b64": "",
                            "eof": True,
                        }
                    else:
                        read_size += len(chunk)
                        if read_size > MAX_NPM_TARBALL_BYTES:
                            raise NpmAcquisitionError("npm tarball exceeded its byte limit.")
                        total_response_bytes += len(chunk)
                        if total_response_bytes > expected_total_limit:
                            raise NpmAcquisitionError(
                                "npm tarballs exceeded their aggregate byte limit."
                            )
                        active_streams[stream_id] = (manager, iterator, url, read_size)
                        value = {
                            "type": "stream_chunk",
                            "id": stream_id,
                            "chunk_b64": base64.b64encode(chunk).decode("ascii"),
                            "eof": False,
                        }
                    encoded = json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n"
                    if len(encoded) > _FRAME_BYTES:
                        raise NpmAcquisitionError("npm stream frame exceeded its protocol limit.")
                    return encoded
                if set(message) != {"type", "id", "method", "url", "headers", "stream"}:
                    raise NpmAcquisitionError("npm worker sent an invalid broker request.")
                request_count += 1
                if (
                    message.get("type") != "request"
                    or type(message.get("id")) is not int
                    or message.get("id") != request_count
                    or request_count > MAX_NPM_TARBALL_COUNT
                    or request_count > len(expected_urls)
                    or message.get("method") != "GET"
                    or message.get("stream") is not True
                    or message.get("url") != expected_urls[request_count - 1]
                    or message.get("headers")
                    != {"accept-encoding": "identity", "user-agent": "Xyst-AI-Agent/0.1"}
                    or len(active_streams) != 0
                ):
                    raise NpmAcquisitionError("npm worker broker request failed validation.")
                url = cast(str, message["url"])
                parsed = urlsplit(url)
                if (
                    parsed.scheme != "https"
                    or parsed.hostname != "registry.npmjs.org"
                    or parsed.port not in {None, 443}
                ):
                    raise NpmAcquisitionError(
                        "npm worker requested a URL outside the fixed registry."
                    )
                manager = client.stream(
                    "GET",
                    url,
                    headers={"Accept-Encoding": "identity", "User-Agent": "Xyst-AI-Agent/0.1"},
                )
                response = await manager.__aenter__()
                try:
                    if (
                        response.status_code != 200
                        or response.url != url
                        or response.url.scheme != "https"
                        or response.url.host != "registry.npmjs.org"
                        or response.url.port not in {None, 443}
                        or response.headers.get("content-encoding", "identity").casefold()
                        != "identity"
                    ):
                        raise NpmAcquisitionError("npm registry tarball response was refused.")
                    content_length = response.headers.get("content-length")
                    if content_length is not None and (
                        not re.fullmatch(r"[0-9]{1,10}", content_length)
                        or not 0 < int(content_length) <= MAX_NPM_TARBALL_BYTES
                    ):
                        raise NpmAcquisitionError("npm tarball response size is invalid.")
                    stream_id = request_count
                    active_streams[stream_id] = (
                        manager,
                        response.aiter_bytes(chunk_size=MAX_NPM_TARBALL_STREAM_CHUNK_BYTES),
                        url,
                        0,
                    )
                    value = {
                        "type": "response",
                        "id": stream_id,
                        "status": 200,
                        "content_type": response.headers.get("content-type", "")[:128],
                        "stream": True,
                    }
                except BaseException:
                    await manager.__aexit__(None, None, None)
                    raise
                encoded = json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n"
                if len(encoded) > _FRAME_BYTES:
                    raise NpmAcquisitionError("npm response exceeded its protocol limit.")
                return encoded

            async def commit_workspace(workspace: Path) -> None:
                if workspace.is_symlink() or workspace.is_junction() or not workspace.is_dir():
                    raise NpmAcquisitionError("npm worker returned an invalid workspace.")
                if {item.name for item in workspace.iterdir()} != {"request.json", "tarballs"}:
                    raise NpmAcquisitionError("npm worker returned unexpected workspace files.")
                tarball_dir = workspace / "tarballs"
                if (
                    tarball_dir.is_symlink()
                    or tarball_dir.is_junction()
                    or not tarball_dir.is_dir()
                ):
                    raise NpmAcquisitionError("npm worker returned an invalid tarball directory.")
                if {item.name for item in tarball_dir.iterdir()} != expected_names:
                    raise NpmAcquisitionError("npm worker returned an incomplete tarball set.")
                paths: dict[str, Path] = {}
                verified: dict[str, NpmVerifiedTarball] = {}
                for index, package in enumerate(lock.packages):
                    path = tarball_dir / f"{index:04d}.tgz"
                    if path.is_symlink() or path.is_junction() or not path.is_file():
                        raise NpmAcquisitionError("npm worker returned an unsafe tarball file.")
                    if path.stat().st_size > MAX_NPM_TARBALL_BYTES:
                        raise NpmAcquisitionError("npm tarball exceeded its byte limit.")
                    raw = path.read_bytes()
                    try:
                        report = verify_npm_tarball(package, raw)
                    except (ValueError, TypeError) as exc:
                        raise NpmAcquisitionError(
                            "npm worker tarball failed independent verification."
                        ) from exc
                    paths[package.package_path] = path
                    verified[package.package_path] = report
                if (
                    sum(item.archive_size_bytes for item in verified.values())
                    > MAX_NPM_TARBALL_TOTAL_BYTES
                ):
                    raise NpmAcquisitionError("npm tarballs exceeded their aggregate byte limit.")
                expected_lock_hash = hashlib.sha256(
                    json.dumps(
                        lock.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest()
                if (
                    result_frame is None
                    or result_frame.get("ok") is not True
                    or result_frame.get("lock_sha256") != expected_lock_hash
                    or result_frame.get("files") != sorted(expected_names)
                ):
                    raise NpmAcquisitionError("npm worker result did not match its lock request.")
                reported = result_frame.get("verified")
                expected_reports = [item.model_dump(mode="json") for item in verified.values()]
                if reported != expected_reports:
                    raise NpmAcquisitionError(
                        "npm worker verification report did not match its tarballs."
                    )
                await on_verified_files(paths, verified)

            try:
                receipt = await transport.run(
                    intent,
                    snapshot,
                    deadline=time.monotonic() + MAX_NPM_TARBALL_SECONDS + 30,
                    on_protocol_frame=broker,
                    on_workspace_result=commit_workspace,
                    isolated_python_worker=source_worker,
                    max_seconds=MAX_NPM_TARBALL_SECONDS,
                    max_output_bytes=4 * 1024 * 1024,
                    max_workspace_bytes=MAX_NPM_TARBALL_TOTAL_BYTES + 4 * 1024 * 1024,
                    streaming_protocol=True,
                    max_protocol_bytes=MAX_NPM_TARBALL_IPC_BYTES,
                )
            except NpmAcquisitionError:
                raise
            except Exception as exc:
                raise NpmAcquisitionError("Isolated npm acquisition did not complete.") from exc
            finally:
                await close_streams()
        if (
            result_frame is None
            or result_frame.get("ok") is not True
            or not receipt.get("succeeded")
        ):
            message = result_frame.get("error") if result_frame is not None else None
            raise NpmAcquisitionError(
                str(message)[:512] if message else "Isolated npm acquisition failed."
            )

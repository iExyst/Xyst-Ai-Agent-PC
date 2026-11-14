"""Bounded PyPI-only acquisition for exact, proposed wheel locks."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from xaa.extensions.procedure_dependencies import (
    MAX_ENVIRONMENT_BYTES,
    MAX_WHEEL_BYTES,
    MAX_WHEELS,
    ProcedurePythonDependencyLock,
    verify_locked_wheel_bytes,
    verify_locked_wheel_paths,
)
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.windows_local_transport import WINDOWS_LOCAL_DISTRIBUTION, WindowsLocalTransport
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot

SIMPLE_API_ACCEPT = "application/vnd.pypi.simple.v1+json"
MAX_SIMPLE_PAGE_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 120
MAX_WHEEL_STREAM_CHUNK_BYTES = 512 * 1024
MAX_ACQUISITION_METADATA_BYTES = 96 * 1024 * 1024
MAX_ACQUISITION_BROKER_BYTES = MAX_ENVIRONMENT_BYTES + MAX_ACQUISITION_METADATA_BYTES
MAX_ACQUISITION_PROTOCOL_BYTES = 512 * 1024 * 1024


class PyPIAcquisitionError(ValueError):
    """A bounded acquisition failed without exposing response contents."""


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PyPIAcquisitionError("PyPI returned duplicate JSON fields.")
        result[key] = value
    return result


def _index_url(project_name: str) -> str:
    return f"https://pypi.org/simple/{canonicalize_name(project_name)}/"


def _pypi_file_url(value: object, filename: str, sha256: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise PyPIAcquisitionError("PyPI returned an invalid wheel URL.")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "files.pythonhosted.org"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or unquote(parsed.path.rsplit("/", 1)[-1]) != filename
    ):
        raise PyPIAcquisitionError("PyPI wheel URL is outside the allowed file host.")
    if parsed.fragment and parsed.fragment != f"sha256={sha256}":
        raise PyPIAcquisitionError("PyPI wheel URL hash does not match the lock.")
    return value.partition("#")[0]


class PyPIWheelAcquirer:
    """Fetch and verify the exact wheels named by a lock from public PyPI."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def acquire(self, lock: ProcedurePythonDependencyLock) -> dict[str, bytes]:
        """Return only a complete, byte-verified wheel set for the supplied lock."""
        total_bytes = sum(wheel.size_bytes for wheel in lock.wheels)
        if total_bytes > 256 * 1024 * 1024:
            raise PyPIAcquisitionError("Locked wheelhouse exceeds its aggregate size limit.")
        if any(wheel.size_bytes > MAX_WHEEL_BYTES for wheel in lock.wheels):
            raise PyPIAcquisitionError("A locked wheel exceeds its size limit.")

        timeout = httpx.Timeout(connect=8, read=20, write=8, pool=8)
        limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
        async with httpx.AsyncClient(
            transport=self._transport,
            trust_env=False,
            verify=True,
            follow_redirects=False,
            timeout=timeout,
            limits=limits,
            headers={"User-Agent": "Xyst-AI-Agent/0.1", "Accept-Encoding": "identity"},
        ) as client:
            try:
                async with asyncio.timeout(MAX_DOWNLOAD_SECONDS):
                    artifacts: dict[str, bytes] = {}
                    for wheel in lock.wheels:
                        page = await self._simple_project(client, wheel.project_name)
                        entry = self._match_file(page, wheel.filename, wheel.sha256)
                        url = _pypi_file_url(entry.get("url"), wheel.filename, wheel.sha256)
                        if entry.get("size") is not None and entry["size"] != wheel.size_bytes:
                            raise PyPIAcquisitionError("PyPI wheel size does not match the lock.")
                        self._check_requires_python(entry.get("requires-python"), lock)
                        if entry.get("yanked"):
                            raise PyPIAcquisitionError("PyPI marks a locked wheel as yanked.")
                        content = await self._download(client, url, wheel.size_bytes)
                        if hashlib.sha256(content).hexdigest() != wheel.sha256:
                            raise PyPIAcquisitionError(
                                "Downloaded wheel SHA-256 does not match the lock."
                            )
                        artifacts[wheel.filename] = content
                    try:
                        return verify_locked_wheel_bytes(lock, artifacts)
                    except ValueError as exc:
                        raise PyPIAcquisitionError(
                            "Downloaded wheel metadata failed lock verification."
                        ) from exc
            except TimeoutError as exc:
                raise PyPIAcquisitionError(
                    "PyPI acquisition exceeded its total time limit."
                ) from exc
            except httpx.HTTPError as exc:
                raise PyPIAcquisitionError(
                    "PyPI acquisition failed before verification completed."
                ) from exc

    async def acquire_to_directory(
        self, lock: ProcedurePythonDependencyLock, destination: Path
    ) -> dict[str, Path]:
        """Download, hash, and inspect wheels on disk without holding a wheel in memory."""
        total_bytes = sum(wheel.size_bytes for wheel in lock.wheels)
        if total_bytes > 256 * 1024 * 1024:
            raise PyPIAcquisitionError("Locked wheelhouse exceeds its aggregate size limit.")
        if any(wheel.size_bytes > MAX_WHEEL_BYTES for wheel in lock.wheels):
            raise PyPIAcquisitionError("A locked wheel exceeds its size limit.")
        destination.mkdir(parents=True, exist_ok=True)
        if (
            destination.is_symlink()
            or destination.is_junction()
            or not destination.is_dir()
            or any(destination.iterdir())
        ):
            raise PyPIAcquisitionError("Wheel staging directory must be a new empty directory.")

        timeout = httpx.Timeout(connect=8, read=20, write=8, pool=8)
        limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
        async with httpx.AsyncClient(
            transport=self._transport,
            trust_env=False,
            verify=True,
            follow_redirects=False,
            timeout=timeout,
            limits=limits,
            headers={"User-Agent": "Xyst-AI-Agent/0.1", "Accept-Encoding": "identity"},
        ) as client:
            try:
                async with asyncio.timeout(MAX_DOWNLOAD_SECONDS):
                    paths: dict[str, Path] = {}
                    for wheel in lock.wheels:
                        page = await self._simple_project(client, wheel.project_name)
                        entry = self._match_file(page, wheel.filename, wheel.sha256)
                        url = _pypi_file_url(entry.get("url"), wheel.filename, wheel.sha256)
                        if entry.get("size") is not None and entry["size"] != wheel.size_bytes:
                            raise PyPIAcquisitionError("PyPI wheel size does not match the lock.")
                        self._check_requires_python(entry.get("requires-python"), lock)
                        if entry.get("yanked"):
                            raise PyPIAcquisitionError("PyPI marks a locked wheel as yanked.")
                        path = destination / wheel.filename
                        await self._download_to_path(
                            client, url, wheel.size_bytes, wheel.sha256, path
                        )
                        paths[wheel.filename] = path
                    try:
                        return verify_locked_wheel_paths(lock, paths)
                    except (OSError, ValueError) as exc:
                        raise PyPIAcquisitionError(
                            "Downloaded wheel files failed lock verification."
                        ) from exc
            except TimeoutError as exc:
                raise PyPIAcquisitionError(
                    "PyPI acquisition exceeded its total time limit."
                ) from exc
            except httpx.HTTPError as exc:
                raise PyPIAcquisitionError(
                    "PyPI acquisition failed before verification completed."
                ) from exc

    async def _simple_project(self, client: httpx.AsyncClient, project: str) -> dict[str, Any]:
        url = _index_url(project)
        if not url.startswith("https://pypi.org/"):
            raise PyPIAcquisitionError("Project URL is outside the fixed PyPI index.")
        async with client.stream("GET", url, headers={"Accept": SIMPLE_API_ACCEPT}) as response:
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
            if (
                response.status_code != 200
                or response.url.scheme != "https"
                or response.url.host != "pypi.org"
                or response.url.port not in {None, 443}
                or content_type != SIMPLE_API_ACCEPT
            ):
                raise PyPIAcquisitionError("PyPI project metadata request was refused.")
            content = await self._read_bounded(response, MAX_SIMPLE_PAGE_BYTES)
        try:
            value: object = json.loads(content, object_pairs_hook=_json_object)
        except (ValueError, UnicodeError) as exc:
            raise PyPIAcquisitionError("PyPI returned invalid project metadata.") from exc
        if not isinstance(value, dict):
            raise PyPIAcquisitionError("PyPI project metadata has an invalid shape.")
        page = cast(dict[str, Any], value)
        if not isinstance(page.get("files"), list):
            raise PyPIAcquisitionError("PyPI project metadata has an invalid shape.")
        if canonicalize_name(str(page.get("name", ""))) != canonicalize_name(project):
            raise PyPIAcquisitionError(
                "PyPI project identity does not match the requested package."
            )
        return page

    @staticmethod
    def _match_file(page: Mapping[str, Any], filename: str, digest: str) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for raw_entry in cast(list[object], page["files"]):
            if not isinstance(raw_entry, dict):
                continue
            entry = cast(dict[str, Any], raw_entry)
            if entry.get("filename") == filename:
                entries.append(entry)
        if len(entries) != 1:
            raise PyPIAcquisitionError("PyPI did not return exactly one matching wheel.")
        entry = entries[0]
        hashes = entry.get("hashes")
        if not isinstance(hashes, dict) or cast(dict[str, Any], hashes).get("sha256") != digest:
            raise PyPIAcquisitionError("PyPI's wheel hash does not match the lock.")
        return entry

    @staticmethod
    def _check_requires_python(value: object, lock: ProcedurePythonDependencyLock) -> None:
        if value is None:
            return
        if not isinstance(value, str) or len(value) > 256:
            raise PyPIAcquisitionError("PyPI returned an invalid Requires-Python value.")
        try:
            compatible = SpecifierSet(value).contains(
                Version(lock.target.environment["python_full_version"]), prereleases=True
            )
        except (InvalidSpecifier, InvalidVersion) as exc:
            raise PyPIAcquisitionError("PyPI returned an invalid Requires-Python value.") from exc
        if not compatible:
            raise PyPIAcquisitionError("Locked wheel does not support the selected Python version.")

    async def _download(self, client: httpx.AsyncClient, url: str, expected_size: int) -> bytes:
        async with client.stream("GET", url) as response:
            if (
                response.status_code != 200
                or response.url.scheme != "https"
                or response.url.host != "files.pythonhosted.org"
                or response.url.port not in {None, 443}
            ):
                raise PyPIAcquisitionError("PyPI wheel download was refused.")
            declared_size = response.headers.get("content-length")
            if declared_size is not None:
                invalid_size = (
                    not re.fullmatch(r"[0-9]{1,10}", declared_size)
                    or int(declared_size) != expected_size
                )
                if invalid_size:
                    raise PyPIAcquisitionError("PyPI wheel response size does not match the lock.")
            return await self._read_bounded(response, expected_size)

    async def _download_to_path(
        self,
        client: httpx.AsyncClient,
        url: str,
        expected_size: int,
        expected_sha256: str,
        path: Path,
    ) -> None:
        digest = hashlib.sha256()
        size = 0
        try:
            async with client.stream("GET", url) as response:
                if (
                    response.status_code != 200
                    or response.url.scheme != "https"
                    or response.url.host != "files.pythonhosted.org"
                    or response.url.port not in {None, 443}
                    or response.url != url
                ):
                    raise PyPIAcquisitionError("PyPI wheel download was refused.")
                declared_size = response.headers.get("content-length")
                if declared_size is not None and (
                    not re.fullmatch(r"[0-9]{1,10}", declared_size)
                    or int(declared_size) != expected_size
                ):
                    raise PyPIAcquisitionError("PyPI wheel response size does not match the lock.")
                with path.open("xb") as output:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > expected_size:
                            raise PyPIAcquisitionError(
                                "PyPI response exceeded its declared size limit."
                            )
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
            if size != expected_size:
                raise PyPIAcquisitionError("PyPI wheel response size does not match the lock.")
            if digest.hexdigest() != expected_sha256:
                raise PyPIAcquisitionError("Downloaded wheel SHA-256 does not match the lock.")
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > maximum:
                raise PyPIAcquisitionError("PyPI response exceeded its declared size limit.")
            content.extend(chunk)
        return bytes(content)


async def acquire_wheelhouse_in_appcontainer(
    lock: ProcedurePythonDependencyLock,
    *,
    on_verified_files: Callable[[dict[str, Path]], Awaitable[None]],
    network_transport: httpx.AsyncBaseTransport | None = None,
    test_worker_executable: Path | None = None,
) -> None:
    """Acquire and verify exact wheel files in the bounded, network-denied worker."""
    from xaa.extensions.pypi_resolver import (
        MAX_BROKER_FRAME_BYTES,
        MAX_BROKER_REQUESTS,
        MAX_RESOLUTION_SECONDS,
        validate_pypi_broker_url,
    )

    if os.name != "nt":
        raise PyPIAcquisitionError(
            "Exact wheel acquisition requires the Windows AppContainer worker."
        )
    if test_worker_executable is not None and network_transport is None:
        raise PyPIAcquisitionError(
            "An alternate worker executable is accepted only with a fixture network transport."
        )
    try:
        transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
    except Exception as exc:
        raise PyPIAcquisitionError("Windows could not prepare isolated wheel acquisition.") from exc

    execution_id = uuid4().hex
    filenames = {wheel.filename for wheel in lock.wheels}
    expected_sizes = {wheel.filename: wheel.size_bytes for wheel in lock.wheels}
    expected_total = sum(expected_sizes.values())
    if expected_total > MAX_ENVIRONMENT_BYTES:
        raise PyPIAcquisitionError("Locked wheelhouse exceeds its aggregate size limit.")

    with tempfile.TemporaryDirectory(prefix=f"xaa-wheel-owner-{execution_id}-") as owner_dir:
        owner_workspace = Path(owner_dir)
        if test_worker_executable is not None:
            try:
                executable = test_worker_executable.resolve(strict=True)
            except OSError as exc:
                raise PyPIAcquisitionError("The frozen acquisition worker is unavailable.") from exc
            if executable.suffix.casefold() != ".exe" or not executable.is_file():
                raise PyPIAcquisitionError("The frozen acquisition worker is invalid.")
            argv = [str(executable), "--pypi-wheel-worker"]
            source_worker = False
        elif getattr(sys, "frozen", False):
            argv = [sys.executable, "--pypi-wheel-worker"]
            source_worker = False
        else:
            engine_root = Path(__file__).resolve().parents[2]
            bootstrap = (
                "import sys; "
                f"sys.path.insert(0, {str(engine_root)!r}); "
                "from xaa.extensions.pypi_wheel_worker import main; "
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
            files=(SnapshotFile(path="request.json", content=request_bytes),),
            directories=(),
        )
        result_frame: dict[str, Any] | None = None
        request_count = 0
        total_response_bytes = 0
        stream_contexts: dict[int, tuple[Any, Any, int, int]] = {}
        network_timeout = httpx.Timeout(connect=8, read=20, write=8, pool=8)
        network_limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
        async with httpx.AsyncClient(
            transport=network_transport,
            trust_env=False,
            verify=True,
            follow_redirects=False,
            timeout=network_timeout,
            limits=network_limits,
            headers={"User-Agent": "Xyst-AI-Agent/0.1", "Accept-Encoding": "identity"},
        ) as client:

            async def close_streams() -> None:
                for manager, _iterator, _size, _read in tuple(stream_contexts.values()):
                    await manager.__aexit__(None, None, None)
                stream_contexts.clear()

            async def broker(frame: bytes) -> bytes | None:
                nonlocal request_count, total_response_bytes, result_frame

                def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                    value: dict[str, Any] = {}
                    for key, item in pairs:
                        if key in value:
                            raise PyPIAcquisitionError(
                                "Wheel worker sent duplicate protocol fields."
                            )
                        value[key] = item
                    return value

                try:
                    decoded: object = json.loads(frame, object_pairs_hook=unique)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise PyPIAcquisitionError(
                        "Wheel worker sent malformed protocol data."
                    ) from exc
                if not isinstance(decoded, dict):
                    raise PyPIAcquisitionError("Wheel worker protocol frame was malformed.")
                message = cast(dict[str, Any], decoded)
                if message.get("type") == "result":
                    valid_success = message.get("ok") is True and set(message) == {
                        "type",
                        "ok",
                        "lock_sha256",
                        "filenames",
                    }
                    valid_failure = message.get("ok") is False and set(message) == {
                        "type",
                        "ok",
                        "error",
                    }
                    if result_frame is not None or not (valid_success or valid_failure):
                        raise PyPIAcquisitionError("Wheel worker returned an invalid result frame.")
                    result_frame = message
                    await close_streams()
                    return None

                if message.get("type") == "stream_next":
                    if set(message) != {"type", "id"} or type(message.get("id")) is not int:
                        raise PyPIAcquisitionError("Wheel worker stream request was malformed.")
                    stream_id = cast(int, message["id"])
                    stream = stream_contexts.get(stream_id)
                    if stream is None:
                        raise PyPIAcquisitionError("Wheel worker requested an unknown stream.")
                    manager, iterator, expected_size, read_size = stream
                    try:
                        chunk = await anext(iterator)
                    except StopAsyncIteration:
                        await manager.__aexit__(None, None, None)
                        del stream_contexts[stream_id]
                        if read_size != expected_size:
                            raise PyPIAcquisitionError(
                                "Downloaded wheel size does not match the lock."
                            )
                        value = {
                            "type": "stream_chunk",
                            "id": stream_id,
                            "chunk_b64": "",
                            "eof": True,
                        }
                    else:
                        read_size += len(chunk)
                        if read_size > expected_size:
                            raise PyPIAcquisitionError(
                                "PyPI wheel response exceeded its locked size."
                            )
                        total_response_bytes += len(chunk)
                        if total_response_bytes > MAX_ACQUISITION_BROKER_BYTES:
                            raise PyPIAcquisitionError(
                                "PyPI acquisition exceeded its response-byte budget."
                            )
                        stream_contexts[stream_id] = (manager, iterator, expected_size, read_size)
                        value = {
                            "type": "stream_chunk",
                            "id": stream_id,
                            "chunk_b64": base64.b64encode(chunk).decode("ascii"),
                            "eof": False,
                        }
                    encoded = json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n"
                    if len(encoded) > MAX_BROKER_FRAME_BYTES:
                        raise PyPIAcquisitionError("PyPI wheel stream frame exceeded its limit.")
                    return encoded

                streaming = message.get("stream") is True
                required: set[str] = {"type", "id", "method", "url", "headers"}
                if streaming:
                    required.add("stream")
                if set(message) != required:
                    raise PyPIAcquisitionError("Wheel worker sent an invalid broker request.")
                request_count += 1
                if request_count > max(MAX_BROKER_REQUESTS, MAX_WHEELS * 2):
                    raise PyPIAcquisitionError("Wheel worker exceeded its network request budget.")
                if (
                    message.get("type") != "request"
                    or type(message.get("id")) is not int
                    or message.get("id") != request_count
                    or message.get("method") != "GET"
                    or not isinstance(message.get("headers"), dict)
                    or len(cast(dict[str, Any], message["headers"])) > 3
                ):
                    raise PyPIAcquisitionError("Wheel worker broker request failed validation.")
                url, body_limit = validate_pypi_broker_url(message.get("url"))
                parsed = urlsplit(url)
                headers: dict[str, str] = {"Accept-Encoding": "identity"}
                worker_headers = cast(dict[str, Any], message["headers"])
                for key, value in worker_headers.items():
                    if (
                        key == "accept"
                        and value == SIMPLE_API_ACCEPT
                        and parsed.hostname == "pypi.org"
                    ):
                        headers["Accept"] = SIMPLE_API_ACCEPT
                    elif (
                        key == "accept"
                        and value == "*/*"
                        and parsed.hostname == "files.pythonhosted.org"
                    ):
                        headers["Accept"] = "*/*"
                    elif key == "user-agent" and value == "Xyst-AI-Agent/0.1":
                        headers["User-Agent"] = "Xyst-AI-Agent/0.1"
                    elif key == "accept-encoding" and value == "identity":
                        continue
                    else:
                        raise PyPIAcquisitionError(
                            "Wheel worker requested unsupported HTTP headers."
                        )

                filename = unquote(parsed.path.rsplit("/", 1)[-1])
                if (
                    parsed.hostname == "files.pythonhosted.org"
                    and filename.endswith(".whl")
                    and not streaming
                ):
                    raise PyPIAcquisitionError("Wheel files must use the bounded stream protocol.")
                if streaming:
                    if (
                        parsed.hostname != "files.pythonhosted.org"
                        or not filename.endswith(".whl")
                        or filename not in expected_sizes
                        or filename.endswith(".whl.metadata")
                        or len(stream_contexts) >= 1
                    ):
                        raise PyPIAcquisitionError(
                            "Wheel worker requested an unapproved wheel stream."
                        )
                    manager = client.stream("GET", url, headers=headers)
                    response = await manager.__aenter__()
                    try:
                        if (
                            response.url != url
                            or response.url.scheme != "https"
                            or response.url.host != "files.pythonhosted.org"
                            or response.url.port not in {None, 443}
                            or response.headers.get("content-encoding", "identity").casefold()
                            != "identity"
                        ):
                            raise PyPIAcquisitionError("PyPI wheel stream was refused.")
                        length = response.headers.get("content-length")
                        expected_size = expected_sizes[filename]
                        if length is not None and (
                            not re.fullmatch(r"[0-9]{1,10}", length) or int(length) != expected_size
                        ):
                            raise PyPIAcquisitionError(
                                "PyPI wheel response size does not match the lock."
                            )
                        stream_contexts[request_count] = (
                            manager,
                            response.aiter_bytes(chunk_size=MAX_WHEEL_STREAM_CHUNK_BYTES),
                            expected_size,
                            0,
                        )
                        value = {
                            "type": "response",
                            "id": request_count,
                            "status": response.status_code,
                            "content_type": response.headers.get("content-type", "")[:256],
                            "stream": True,
                            "content_length": expected_size,
                        }
                    except BaseException:
                        await manager.__aexit__(None, None, None)
                        raise
                else:
                    async with client.stream("GET", url, headers=headers) as response:
                        content_type = response.headers.get("content-type", "")[:256]
                        content = bytearray()
                        if response.status_code == 200:
                            if (
                                response.headers.get("content-encoding", "identity").casefold()
                                != "identity"
                            ):
                                raise PyPIAcquisitionError("PyPI response encoding is unsupported.")
                            async for chunk in response.aiter_bytes():
                                if len(content) + len(chunk) > body_limit:
                                    raise PyPIAcquisitionError(
                                        "PyPI response exceeded its declared size limit."
                                    )
                                content.extend(chunk)
                    total_response_bytes += len(content)
                    if total_response_bytes > MAX_ACQUISITION_BROKER_BYTES:
                        raise PyPIAcquisitionError(
                            "PyPI acquisition exceeded its response-byte budget."
                        )
                    value = {
                        "type": "response",
                        "id": request_count,
                        "status": response.status_code,
                        "content_type": content_type,
                        "body_b64": base64.b64encode(content).decode("ascii"),
                    }
                encoded = json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n"
                if len(encoded) > MAX_BROKER_FRAME_BYTES:
                    raise PyPIAcquisitionError("PyPI response exceeded the worker IPC limit.")
                return encoded

            async def commit_workspace(workspace: Path) -> None:
                if workspace.is_symlink() or workspace.is_junction() or not workspace.is_dir():
                    raise PyPIAcquisitionError("Acquisition worker returned an invalid workspace.")
                expected_children = {"request.json", "wheels"}
                if {item.name for item in workspace.iterdir()} != expected_children:
                    raise PyPIAcquisitionError(
                        "Acquisition worker returned unexpected workspace files."
                    )
                request_path = workspace / "request.json"
                wheel_dir = workspace / "wheels"
                if (
                    request_path.is_symlink()
                    or request_path.is_junction()
                    or not request_path.is_file()
                    or wheel_dir.is_symlink()
                    or wheel_dir.is_junction()
                    or not wheel_dir.is_dir()
                ):
                    raise PyPIAcquisitionError("Acquisition worker returned an invalid workspace.")
                if {item.name for item in wheel_dir.iterdir()} != filenames:
                    raise PyPIAcquisitionError(
                        "Acquisition worker returned an incomplete wheelhouse."
                    )
                paths = {name: wheel_dir / name for name in filenames}
                try:
                    verify_locked_wheel_paths(lock, paths)
                except (OSError, ValueError) as exc:
                    raise PyPIAcquisitionError(
                        "Acquisition worker files failed independent verification."
                    ) from exc
                if (
                    result_frame is None
                    or result_frame.get("ok") is not True
                    or result_frame.get("lock_sha256")
                    != hashlib.sha256(lock.canonical_json().encode("utf-8")).hexdigest()
                    or result_frame.get("filenames") != sorted(filenames)
                ):
                    raise PyPIAcquisitionError(
                        "Acquisition worker returned a mismatched lock result."
                    )
                await on_verified_files(paths)

            try:
                receipt = await transport.run(
                    intent,
                    snapshot,
                    deadline=time.monotonic() + MAX_RESOLUTION_SECONDS + 30,
                    on_protocol_frame=broker,
                    on_workspace_result=commit_workspace,
                    isolated_python_worker=source_worker,
                    max_seconds=MAX_RESOLUTION_SECONDS,
                    max_output_bytes=4 * 1024 * 1024,
                    max_workspace_bytes=MAX_ENVIRONMENT_BYTES + 4 * 1024 * 1024,
                    streaming_protocol=True,
                    max_protocol_bytes=MAX_ACQUISITION_PROTOCOL_BYTES,
                )
            except Exception as exc:
                raise PyPIAcquisitionError("Isolated wheel acquisition did not complete.") from exc
            finally:
                await close_streams()
        if result_frame is None or result_frame.get("ok") is not True:
            message = result_frame.get("error") if result_frame is not None else None
            if message:
                raise PyPIAcquisitionError(message[:512])
            raise PyPIAcquisitionError("Isolated wheel acquisition returned no verified result.")
        if not receipt.get("succeeded"):
            raise PyPIAcquisitionError(
                "Isolated wheel acquisition exited without a verified result."
            )

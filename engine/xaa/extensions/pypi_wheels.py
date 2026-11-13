"""Bounded PyPI-only acquisition for exact, proposed wheel locks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from xaa.extensions.procedure_dependencies import (
    MAX_WHEEL_BYTES,
    ProcedurePythonDependencyLock,
    verify_locked_wheel_bytes,
)

SIMPLE_API_ACCEPT = "application/vnd.pypi.simple.v1+json"
MAX_SIMPLE_PAGE_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 180


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

    @staticmethod
    async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > maximum:
                raise PyPIAcquisitionError("PyPI response exceeded its declared size limit.")
            content.extend(chunk)
        return bytes(content)

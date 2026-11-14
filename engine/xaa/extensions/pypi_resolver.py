"""Resolve imported Python requirements to a bounded, reviewable PyPI wheel lock.

The result contains index metadata only. The separate acquisition action downloads
the selected wheel bytes and verifies their hashes and dependency metadata before
the owner can approve the lock.
"""

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
from collections.abc import Mapping
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import parse_tag
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from xaa.extensions.procedure_dependencies import (
    MAX_DEPENDENCIES,
    MAX_ENVIRONMENT_BYTES,
    MAX_REQUIREMENTS_PER_WHEEL,
    MAX_WHEEL_BYTES,
    MAX_WHEELS,
    LockedPythonWheel,
    ProcedurePythonDependencyLock,
    PythonDependencyTarget,
)
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.windows_local_transport import WINDOWS_LOCAL_DISTRIBUTION, WindowsLocalTransport
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot

SIMPLE_API_ACCEPT = "application/vnd.pypi.simple.v1+json"
MAX_SIMPLE_PAGE_BYTES = 2 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_PROJECTS = 64
MAX_CANDIDATES_PER_PROJECT = 16
MAX_METADATA_REQUESTS = 128
MAX_SOLVER_STEPS = 512
MAX_RESOLUTION_SECONDS = 120
MAX_FILES_PER_PROJECT = 20_000
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class PyPIResolutionError(ValueError):
    """Resolution stopped without producing a complete dependency lock."""


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PyPIResolutionError("PyPI returned duplicate JSON fields.")
        result[key] = value
    return result


@dataclass(frozen=True)
class _Candidate:
    wheel: LockedPythonWheel
    version: Version


@dataclass(frozen=True)
class _WheelFile:
    version: Version
    tag_rank: int
    filename: str
    entry: dict[str, Any]
    sha256: str
    size_bytes: int


class PyPIWheelResolver:
    """Resolve target-specific requirements using bounded PEP 691/658 metadata."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._pages: dict[str, dict[str, Any]] = {}
        self._files: dict[str, tuple[_WheelFile, ...]] = {}
        self._candidate_metadata: dict[str, _Candidate | None] = {}
        self._metadata_requests = 0
        self._steps = 0
        self._tags: set[Any] = set()
        self._tag_rank: dict[Any, int] = {}
        self._marker_environment: dict[str, str] = {}
        self._client: httpx.AsyncClient | None = None

    async def resolve(
        self,
        *,
        skill_id: str,
        skill_version: int,
        source_sha256: str,
        target: PythonDependencyTarget,
        requirements: tuple[str, ...] | list[str],
    ) -> ProcedurePythonDependencyLock:
        """Return a complete lock or raise; no partial candidate set is returned."""
        if not requirements or len(requirements) > MAX_DEPENDENCIES:
            raise PyPIResolutionError("Resolution requires 1 to 64 declared requirements.")
        if sum(len(item) for item in requirements) > 16 * 1024:
            raise PyPIResolutionError("The imported requirement set exceeds its size limit.")
        parsed_values = tuple(self._parse_requirement(value) for value in requirements)
        parsed = tuple({str(item): item for item in parsed_values}.values())
        if not parsed:
            raise PyPIResolutionError("Resolution requires at least one unique requirement.")
        if self._transport is None:
            return await resolve_in_appcontainer(
                skill_id=skill_id,
                skill_version=skill_version,
                source_sha256=source_sha256,
                target=target,
                requirements=tuple(str(item) for item in parsed),
            )

        self._pages.clear()
        self._files.clear()
        self._candidate_metadata.clear()
        self._metadata_requests = 0
        self._steps = 0
        self._tag_rank = {}
        for rank, value in enumerate(target.compatible_wheel_tags):
            for tag in parse_tag(value):
                self._tag_rank.setdefault(tag, rank)
        self._tags = set(self._tag_rank)
        self._marker_environment = cast(dict[str, str], default_environment())
        self._marker_environment.update(target.environment)

        timeout = httpx.Timeout(connect=8, read=12, write=8, pool=8)
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
            self._client = client
            try:
                async with asyncio.timeout(MAX_RESOLUTION_SECONDS):
                    solved = await self._search(list(parsed), {})
            except TimeoutError as exc:
                raise PyPIResolutionError(
                    "PyPI dependency resolution exceeded its time limit."
                ) from exc
            except httpx.HTTPError as exc:
                raise PyPIResolutionError("PyPI metadata could not be fetched safely.") from exc
            finally:
                self._client = None

        if solved is None:
            raise PyPIResolutionError(
                "No compatible wheel set satisfies all declared requirements."
            )
        if not solved or len(solved) > MAX_WHEELS:
            raise PyPIResolutionError("The resolved package set exceeds its supported size.")
        wheels = tuple(item.wheel for item in solved.values())
        if sum(item.size_bytes for item in wheels) > MAX_ENVIRONMENT_BYTES:
            raise PyPIResolutionError("The resolved wheelhouse exceeds its aggregate size limit.")
        try:
            return ProcedurePythonDependencyLock(
                schema_version=1,
                skill_id=skill_id,
                skill_version=skill_version,
                source_sha256=source_sha256,
                target=target,
                requirements=tuple(str(item) for item in parsed),
                wheels=wheels,
            )
        except ValueError as exc:
            raise PyPIResolutionError(
                "PyPI metadata did not produce a complete, valid dependency graph."
            ) from exc

    @staticmethod
    def _parse_requirement(value: str) -> Requirement:
        if not value or len(value) > 256:
            raise PyPIResolutionError("An imported Python requirement is invalid.")
        try:
            requirement = Requirement(value)
        except InvalidRequirement as exc:
            raise PyPIResolutionError("An imported Python requirement is invalid.") from exc
        if requirement.url is not None or requirement.extras:
            raise PyPIResolutionError("Direct URLs and package extras are not supported.")
        return requirement

    async def _search(
        self, pending: list[Requirement], selected: dict[str, _Candidate]
    ) -> dict[str, _Candidate] | None:
        self._steps += 1
        if self._steps > MAX_SOLVER_STEPS:
            raise PyPIResolutionError("Dependency resolution exceeded its search limit.")
        if not pending:
            return selected
        requirement, *remaining = pending
        if requirement.marker is not None and not requirement.marker.evaluate(
            self._marker_environment
        ):
            return await self._search(remaining, selected)

        project = canonicalize_name(requirement.name)
        current = selected.get(project)
        if current is not None:
            if self._satisfies(current.version, requirement.specifier):
                return await self._search(remaining, selected)
            return None

        candidates = await self._project_candidates(project, requirement.specifier)
        for candidate in candidates:
            next_selected = dict(selected)
            next_selected[project] = candidate
            dependencies: list[Requirement] = []
            try:
                dependencies = [
                    self._parse_requirement(value) for value in candidate.wheel.requires_dist
                ]
            except PyPIResolutionError:
                continue
            if len(selected) + 1 > MAX_WHEELS:
                raise PyPIResolutionError("The resolved package set exceeds its package limit.")
            result = await self._search(dependencies + remaining, next_selected)
            if result is not None:
                return result
        return None

    @staticmethod
    def _satisfies(version: Version, specifier: SpecifierSet) -> bool:
        return not specifier or specifier.contains(version, prereleases=True)

    async def _project_candidates(
        self, project: str, specifier: SpecifierSet
    ) -> tuple[_Candidate, ...]:
        if project not in self._files:
            self._files[project] = await self._load_project_files(project)
        matching = [
            item for item in self._files[project] if self._satisfies(item.version, specifier)
        ]
        stable = [
            item
            for item in matching
            if not item.version.is_prerelease and not item.version.is_devrelease
        ]
        matching = stable or matching
        matching.sort(key=lambda item: (item.version, -item.tag_rank, item.filename), reverse=True)
        candidates: list[_Candidate] = []
        seen_versions: set[Version] = set()
        versions_tried = 0
        for item in matching:
            if item.version in seen_versions:
                continue
            seen_versions.add(item.version)
            versions_tried += 1
            if versions_tried > MAX_CANDIDATES_PER_PROJECT:
                break
            alternatives = [
                candidate for candidate in matching if candidate.version == item.version
            ]
            for candidate_file in alternatives:
                candidate = self._candidate_metadata.get(candidate_file.filename)
                if candidate_file.filename not in self._candidate_metadata:
                    try:
                        candidate = await self._build_candidate(candidate_file)
                    except (PyPIResolutionError, ValueError):
                        candidate = None
                    self._candidate_metadata[candidate_file.filename] = candidate
                if candidate is not None:
                    candidates.append(candidate)
                    break
        return tuple(candidates)

    async def _load_project_files(self, project: str) -> tuple[_WheelFile, ...]:
        if len(self._pages) >= MAX_PROJECTS and project not in self._pages:
            raise PyPIResolutionError("Resolution exceeded its distinct package limit.")
        client = self._client
        if client is None:
            raise PyPIResolutionError("PyPI resolver was not initialized.")
        page = await self._simple_project(client, project)
        raw_files = cast(list[object], page["files"])
        if len(raw_files) > MAX_FILES_PER_PROJECT:
            raise PyPIResolutionError("PyPI project contains too many files to resolve safely.")
        entries: list[_WheelFile] = []
        for raw in raw_files:
            if not isinstance(raw, dict):
                continue
            entry = cast(dict[str, Any], raw)
            if entry.get("yanked"):
                continue
            filename = entry.get("filename")
            if not isinstance(filename, str) or len(filename) > 255:
                continue
            try:
                name, version, _build, wheel_tags = parse_wheel_filename(filename)
            except (InvalidWheelFilename, InvalidVersion):
                continue
            if name != canonicalize_name(project):
                continue
            if not self._tags.intersection(wheel_tags):
                continue
            hashes_value = entry.get("hashes")
            hashes = cast(dict[str, object], hashes_value) if isinstance(hashes_value, dict) else {}
            sha256 = hashes.get("sha256")
            size = entry.get("size")
            if (
                not isinstance(sha256, str)
                or _SHA256.fullmatch(sha256) is None
                or type(size) is not int
                or size <= 0
                or size > MAX_WHEEL_BYTES
            ):
                continue
            if not self._requires_python_compatible(entry.get("requires-python")):
                continue
            if not self._metadata_is_advertised(entry):
                continue
            try:
                self._file_url(entry.get("url"), filename, sha256)
            except PyPIResolutionError:
                continue
            tag_rank = min(self._tag_rank[tag] for tag in wheel_tags if tag in self._tag_rank)
            entries.append(
                _WheelFile(
                    version=version,
                    tag_rank=tag_rank,
                    filename=filename,
                    entry=entry,
                    sha256=sha256,
                    size_bytes=size,
                )
            )
        return tuple(entries)

    async def _build_candidate(self, item: _WheelFile) -> _Candidate:
        client = self._client
        if client is None:
            raise PyPIResolutionError("PyPI resolver was not initialized.")
        requires_dist = await self._wheel_requirements(
            client, item.entry, item.filename, item.sha256
        )
        try:
            project_name, version, _build, _tags = parse_wheel_filename(item.filename)
            wheel = LockedPythonWheel(
                filename=item.filename,
                project_name=canonicalize_name(str(project_name)),
                version=str(version),
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                requires_dist=requires_dist,
            )
        except (InvalidWheelFilename, InvalidVersion, ValueError) as exc:
            raise PyPIResolutionError("PyPI wheel metadata did not match its filename.") from exc
        return _Candidate(wheel=wheel, version=item.version)

    async def _simple_project(self, client: httpx.AsyncClient, project: str) -> dict[str, Any]:
        url = f"https://pypi.org/simple/{project}/"
        async with client.stream("GET", url, headers={"Accept": SIMPLE_API_ACCEPT}) as response:
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
            if (
                response.status_code != 200
                or response.url.scheme != "https"
                or response.url.host != "pypi.org"
                or response.url.port not in {None, 443}
                or content_type != SIMPLE_API_ACCEPT
            ):
                raise PyPIResolutionError("PyPI project metadata request was refused.")
            content = await self._read_bounded(response, MAX_SIMPLE_PAGE_BYTES)
        try:
            value: object = json.loads(content, object_pairs_hook=_json_object)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise PyPIResolutionError("PyPI returned invalid project metadata.") from exc
        if not isinstance(value, dict):
            raise PyPIResolutionError("PyPI project metadata has an invalid shape.")
        page = cast(dict[str, Any], value)
        if not isinstance(page.get("files"), list) or canonicalize_name(
            str(page.get("name", ""))
        ) != canonicalize_name(project):
            raise PyPIResolutionError("PyPI project identity does not match the request.")
        self._pages[project] = page
        return page

    async def _wheel_requirements(
        self, client: httpx.AsyncClient, entry: Mapping[str, Any], filename: str, sha256: str
    ) -> tuple[str, ...]:
        advertised = entry.get("core-metadata", entry.get("dist-info-metadata"))
        metadata_hash: str | None = None
        if isinstance(advertised, dict):
            raw_hash = cast(dict[str, object], advertised).get("sha256")
            if not isinstance(raw_hash, str) or _SHA256.fullmatch(raw_hash) is None:
                raise PyPIResolutionError("PyPI metadata omitted a valid SHA-256 hash.")
            metadata_hash = raw_hash
        if advertised is not True and not isinstance(advertised, dict):
            raise PyPIResolutionError("PyPI does not publish metadata for this wheel.")

        self._metadata_requests += 1
        if self._metadata_requests > MAX_METADATA_REQUESTS:
            raise PyPIResolutionError("Resolution exceeded its metadata request limit.")
        url = self._metadata_url(entry.get("url"), filename, sha256)
        async with client.stream("GET", url) as response:
            if (
                response.status_code != 200
                or response.url.scheme != "https"
                or response.url.host != "files.pythonhosted.org"
                or response.url.port not in {None, 443}
            ):
                raise PyPIResolutionError("PyPI wheel metadata request was refused.")
            content = await self._read_bounded(response, MAX_METADATA_BYTES)
        if metadata_hash is not None and hashlib.sha256(content).hexdigest() != metadata_hash:
            raise PyPIResolutionError("PyPI wheel metadata SHA-256 did not match the index.")
        message = BytesParser(policy=policy.default).parsebytes(content)
        if message.defects or len(content) == 0:
            raise PyPIResolutionError("PyPI returned malformed wheel metadata.")
        names = message.get_all("Name", [])
        versions = message.get_all("Version", [])
        try:
            parsed_name, parsed_version, _build, _tags = parse_wheel_filename(filename)
            metadata_version = Version(versions[0]) if len(versions) == 1 else None
        except (InvalidWheelFilename, InvalidVersion) as exc:
            raise PyPIResolutionError(
                "PyPI wheel filename or metadata version is invalid."
            ) from exc
        if (
            len(names) != 1
            or not isinstance(names[0], str)
            or canonicalize_name(names[0]) != parsed_name
            or metadata_version != parsed_version
        ):
            raise PyPIResolutionError("PyPI wheel metadata identity did not match its file.")
        requirements: list[str] = []
        raw_requirements = message.get_all("Requires-Dist", [])
        if len(raw_requirements) > MAX_REQUIREMENTS_PER_WHEEL:
            raise PyPIResolutionError("Wheel dependency metadata exceeds its package limit.")
        for raw in raw_requirements:
            if not isinstance(raw, str) or len(raw) > 512:
                raise PyPIResolutionError("Wheel dependency metadata is invalid or too large.")
            try:
                requirement = Requirement(raw)
            except InvalidRequirement as exc:
                raise PyPIResolutionError("Wheel dependency metadata is malformed.") from exc
            if requirement.url is not None or requirement.extras:
                raise PyPIResolutionError("Wheel metadata contains unsupported extras or URLs.")
            requirements.append(str(requirement))
        return tuple(sorted(requirements, key=str.casefold))

    def _requires_python_compatible(self, value: object) -> bool:
        if value is None:
            return True
        if not isinstance(value, str) or len(value) > 256:
            return False
        try:
            specifier = SpecifierSet(value)
            version = Version(self._marker_environment["python_full_version"])
        except (InvalidSpecifier, InvalidVersion, KeyError):
            return False
        return specifier.contains(version, prereleases=True)

    @staticmethod
    def _metadata_is_advertised(entry: Mapping[str, Any]) -> bool:
        value = entry.get("core-metadata", entry.get("dist-info-metadata"))
        return value is True or isinstance(value, dict)

    @staticmethod
    def _file_url(value: object, filename: str, sha256: str) -> str:
        if not isinstance(value, str) or len(value) > 2048:
            raise PyPIResolutionError("PyPI returned an invalid wheel URL.")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "files.pythonhosted.org"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or unquote(parsed.path.rsplit("/", 1)[-1]) != filename
            or (parsed.fragment and parsed.fragment != f"sha256={sha256}")
        ):
            raise PyPIResolutionError("PyPI wheel URL is outside the allowed file host.")
        return urlunsplit(("https", "files.pythonhosted.org", parsed.path, "", ""))

    @classmethod
    def _metadata_url(cls, value: object, filename: str, sha256: str) -> str:
        file_url = cls._file_url(value, filename, sha256)
        return f"{file_url}.metadata"

    @staticmethod
    async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > maximum:
                raise PyPIResolutionError("PyPI response exceeded its declared size limit.")
            content.extend(chunk)
        return bytes(content)


MAX_BROKER_REQUESTS = MAX_PROJECTS + MAX_METADATA_REQUESTS
MAX_BROKER_TOTAL_BYTES = 96 * 1024 * 1024
MAX_BROKER_FRAME_BYTES = 3 * 1024 * 1024


def validate_pypi_broker_url(value: object) -> tuple[str, int]:
    if not isinstance(value, str) or len(value) > 2048:
        raise PyPIResolutionError("Resolver worker requested an invalid PyPI URL.")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise PyPIResolutionError("Resolver worker requested an invalid PyPI port.") from exc
    decoded_path = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"pypi.org", "files.pythonhosted.org"}
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in decoded_path
        or any(ord(character) < 32 for character in decoded_path)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
    ):
        raise PyPIResolutionError("Resolver worker requested a URL outside the PyPI broker.")
    if parsed.hostname == "pypi.org":
        if not re.fullmatch(
            r"/simple/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/", parsed.path
        ) or parsed.path.endswith("//"):
            raise PyPIResolutionError("Resolver worker requested an invalid PyPI project page.")
        return urlunsplit(("https", "pypi.org", parsed.path, "", "")), MAX_SIMPLE_PAGE_BYTES
    if not parsed.path.startswith("/packages/") or not parsed.path.endswith(".whl.metadata"):
        raise PyPIResolutionError("Resolver worker requested unsupported PyPI file metadata.")
    filename = unquote(parsed.path.rsplit("/", 1)[-1])
    if (
        len(filename) > 264
        or not filename.endswith(".whl.metadata")
        or "/" in filename
        or "\\" in filename
    ):
        raise PyPIResolutionError("Resolver worker requested invalid wheel metadata.")
    return (
        urlunsplit(("https", "files.pythonhosted.org", parsed.path, "", "")),
        MAX_METADATA_BYTES,
    )


async def resolve_in_appcontainer(
    *,
    skill_id: str,
    skill_version: int,
    source_sha256: str,
    target: PythonDependencyTarget,
    requirements: tuple[str, ...],
    network_transport: httpx.AsyncBaseTransport | None = None,
    test_worker_executable: Path | None = None,
) -> ProcedurePythonDependencyLock:
    try:
        return await _resolve_in_appcontainer_impl(
            skill_id=skill_id,
            skill_version=skill_version,
            source_sha256=source_sha256,
            target=target,
            requirements=requirements,
            network_transport=network_transport,
            test_worker_executable=test_worker_executable,
        )
    except PyPIResolutionError:
        raise
    except Exception as exc:
        raise PyPIResolutionError("Windows could not complete isolated PyPI resolution.") from exc


async def _resolve_in_appcontainer_impl(
    *,
    skill_id: str,
    skill_version: int,
    source_sha256: str,
    target: PythonDependencyTarget,
    requirements: tuple[str, ...],
    network_transport: httpx.AsyncBaseTransport | None,
    test_worker_executable: Path | None,
) -> ProcedurePythonDependencyLock:
    """Run the metadata parser/solver in X.A.A.'s bounded no-network container."""
    if os.name != "nt":
        raise PyPIResolutionError(
            "Python dependency resolution requires the Windows AppContainer worker."
        )
    if test_worker_executable is not None and network_transport is None:
        raise PyPIResolutionError(
            "An alternate resolver executable is accepted only by fixture tests."
        )
    try:
        transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
    except Exception as exc:
        raise PyPIResolutionError("Windows could not prepare the isolated resolver.") from exc

    execution_id = uuid4().hex
    with tempfile.TemporaryDirectory(prefix=f"xaa-resolver-owner-{execution_id}-") as workspace:
        if test_worker_executable is not None:
            try:
                executable = test_worker_executable.resolve(strict=True)
            except OSError as exc:
                raise PyPIResolutionError(
                    "The frozen resolver test executable is unavailable."
                ) from exc
            if executable.suffix.casefold() != ".exe" or not executable.is_file():
                raise PyPIResolutionError("The frozen resolver test executable is invalid.")
            argv = [str(executable), "--pypi-resolver-worker"]
            source_worker = False
        elif getattr(sys, "frozen", False):
            argv = [sys.executable, "--pypi-resolver-worker"]
            source_worker = False
        else:
            engine_root = Path(__file__).resolve().parents[2]
            bootstrap = (
                "import sys; "
                f"sys.path.insert(0, {str(engine_root)!r}); "
                "from xaa.extensions.pypi_resolver_worker import main; "
                "raise SystemExit(main())"
            )
            argv = [sys.executable, "-s", "-c", bootstrap]
            source_worker = True
        intent = CommandIntent(
            execution_id=execution_id,
            backend="windows-appcontainer-v1",
            distribution=WINDOWS_LOCAL_DISTRIBUTION,
            profile_sha256=transport.profile_sha256,
            workspace=workspace,
            argv=argv,
            max_seconds=30,
        )
        normalized_requirements = tuple(sorted(set(requirements), key=str.casefold))
        request_bytes = json.dumps(
            {
                "skill_id": skill_id,
                "skill_version": skill_version,
                "source_sha256": source_sha256,
                "target": target.model_dump(mode="json"),
                "requirements": list(normalized_requirements),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        snapshot = WorkspaceSnapshot(
            files=(SnapshotFile(path="request.json", content=request_bytes),),
            directories=(),
        )
        response_result: dict[str, Any] | None = None
        request_count = 0
        total_bytes = 0
        timeout = MAX_RESOLUTION_SECONDS
        network_timeout = httpx.Timeout(connect=8, read=12, write=8, pool=8)
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

            async def broker(frame: bytes) -> bytes | None:
                nonlocal request_count, total_bytes, response_result

                def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                    result: dict[str, Any] = {}
                    for key, value in pairs:
                        if key in result:
                            raise PyPIResolutionError(
                                "Resolver worker sent duplicate protocol fields."
                            )
                        result[key] = value
                    return result

                try:
                    value: object = json.loads(frame, object_pairs_hook=unique)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise PyPIResolutionError(
                        "Resolver worker sent malformed protocol data."
                    ) from exc
                if not isinstance(value, dict):
                    raise PyPIResolutionError("Resolver worker protocol frame was not an object.")
                message = cast(dict[str, Any], value)
                if message.get("type") == "result":
                    valid_success = message.get("ok") is True and set(message) == {
                        "type",
                        "ok",
                        "lock",
                    }
                    valid_failure = message.get("ok") is False and set(message) == {
                        "type",
                        "ok",
                        "error",
                    }
                    if response_result is not None or not (valid_success or valid_failure):
                        raise PyPIResolutionError(
                            "Resolver worker returned an invalid result frame."
                        )
                    response_result = message
                    return None
                if set(message) != {"type", "id", "method", "url", "headers"}:
                    raise PyPIResolutionError("Resolver worker sent an invalid broker request.")
                request_count += 1
                if request_count > MAX_BROKER_REQUESTS:
                    raise PyPIResolutionError(
                        "Resolver worker exceeded its network request budget."
                    )
                if (
                    message.get("type") != "request"
                    or type(message.get("id")) is not int
                    or message.get("id") != request_count
                    or message.get("method") != "GET"
                    or not isinstance(message.get("headers"), dict)
                    or len(cast(dict[str, Any], message["headers"])) > 3
                ):
                    raise PyPIResolutionError("Resolver worker broker request failed validation.")
                url, body_limit = validate_pypi_broker_url(message.get("url"))
                headers: dict[str, str] = {"Accept-Encoding": "identity"}
                worker_headers = cast(dict[str, Any], message["headers"])
                for key, value in worker_headers.items():
                    if (
                        key == "accept"
                        and value == SIMPLE_API_ACCEPT
                        and url.startswith("https://pypi.org/")
                    ):
                        headers["Accept"] = SIMPLE_API_ACCEPT
                    elif (
                        key == "accept"
                        and value == "*/*"
                        and url.startswith("https://files.pythonhosted.org/")
                    ):
                        headers["Accept"] = "*/*"
                    elif key == "user-agent" and value == "Xyst-AI-Agent/0.1":
                        headers["User-Agent"] = "Xyst-AI-Agent/0.1"
                    elif key == "accept-encoding" and value == "identity":
                        continue
                    else:
                        raise PyPIResolutionError(
                            "Resolver worker requested unsupported HTTP headers."
                        )
                content = bytearray()
                status = 0
                content_type = ""
                async with client.stream("GET", url, headers=headers) as response:
                    status = response.status_code
                    content_type = response.headers.get("content-type", "")[:256]
                    if status == 200:
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > body_limit:
                                raise PyPIResolutionError(
                                    "PyPI response exceeded its declared size limit."
                                )
                            content.extend(chunk)
                total_bytes += len(content)
                if total_bytes > MAX_BROKER_TOTAL_BYTES:
                    raise PyPIResolutionError(
                        "PyPI resolution exceeded its aggregate metadata limit."
                    )
                body_b64 = base64.b64encode(content).decode("ascii")
                reply = {
                    "type": "response",
                    "id": request_count,
                    "status": status,
                    "content_type": content_type,
                    "body_b64": body_b64,
                }
                encoded = json.dumps(reply, separators=(",", ":")).encode("ascii") + b"\n"
                if len(encoded) > MAX_BROKER_FRAME_BYTES:
                    raise PyPIResolutionError("PyPI response exceeded the resolver IPC limit.")
                return encoded

            try:
                receipt = await transport.run(
                    intent,
                    snapshot,
                    deadline=time.monotonic() + timeout + 10,
                    on_protocol_frame=broker,
                    isolated_python_worker=source_worker,
                    max_seconds=timeout,
                    max_output_bytes=4 * 1024 * 1024,
                )
            except Exception as exc:
                raise PyPIResolutionError("The isolated PyPI resolver did not complete.") from exc

    if response_result is None:
        raise PyPIResolutionError("The isolated PyPI resolver omitted its result.")
    if response_result.get("type") != "result" or response_result.get("ok") is not True:
        message = response_result.get("error")
        if isinstance(message, str) and message:
            raise PyPIResolutionError(message[:512])
        raise PyPIResolutionError("The isolated PyPI resolver rejected the package set.")
    if not receipt.get("succeeded"):
        raise PyPIResolutionError("The isolated PyPI resolver exited without a verified result.")
    lock_value = response_result.get("lock")
    try:
        lock = ProcedurePythonDependencyLock.model_validate(lock_value, strict=True)
    except (ValueError, TypeError) as exc:
        raise PyPIResolutionError("The isolated PyPI resolver returned an invalid lock.") from exc
    if (
        lock.skill_id != skill_id
        or lock.skill_version != skill_version
        or lock.source_sha256 != source_sha256
        or lock.target != target
        or lock.requirements != normalized_requirements
    ):
        raise PyPIResolutionError("The isolated PyPI resolver returned a mismatched lock identity.")
    return lock

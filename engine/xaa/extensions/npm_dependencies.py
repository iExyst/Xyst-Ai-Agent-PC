"""Validate imported npm v3 lockfiles without fetching or installing packages."""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import io
import json
import re
import tarfile
import uuid
from collections.abc import Callable, Iterable
from typing import Any, Literal, Self, cast
from urllib.parse import unquote, urlsplit

import nodesemver
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_LOCKFILE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_MANIFEST_BYTES = 1024 * 1024
MAX_LOCKED_PACKAGES = 512
MAX_NPM_DEPENDENCY_EDGES = 20_000
MAX_PACKAGE_PATH_BYTES = 1024
MAX_NPM_TARBALL_BYTES = 64 * 1024 * 1024
MAX_NPM_TARBALL_TOTAL_BYTES = 256 * 1024 * 1024
MAX_NPM_TARBALL_MEMBERS = 20_000
MAX_NPM_TARBALL_MEMBER_BYTES = 64 * 1024 * 1024
MAX_NPM_TARBALL_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_NPM_TARBALL_STREAM_BYTES = 320 * 1024 * 1024
MAX_NPM_TARBALL_PACKAGE_JSON_BYTES = 1024 * 1024
_NPM_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]{0,127}/)?[a-z0-9][a-z0-9._-]{0,127}$")
_SEMVER = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_RANGE = re.compile(r"^[A-Za-z0-9.*+^~<>=| -]{1,128}$")
_NPM_SPEC = re.compile(
    r"^(?P<name>(?:@[a-z0-9][a-z0-9._-]{0,127}/)?[a-z0-9][a-z0-9._-]{0,127})"
    r"(?:@(?P<range>[A-Za-z0-9.*+^~<>=| -]{1,128}))?$"
)
_NODE_SEMVER: Any = cast(Any, nodesemver)
_NODE_SEMVER_VALID = cast(Callable[[str, bool], str | None], _NODE_SEMVER.valid)
_NODE_SEMVER_VALID_RANGE = cast(Callable[[str, bool], str | None], _NODE_SEMVER.valid_range)
_NODE_SEMVER_SATISFIES = cast(Callable[[str, str], bool], _NODE_SEMVER.satisfies)


class NpmRootDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: str = Field(min_length=1, max_length=256)
    version_range: str = Field(min_length=1, max_length=128)
    kind: str = Field(
        pattern=r"^(?:dependencies|devDependencies|optionalDependencies|peerDependencies)$"
    )

    @model_validator(mode="after")
    def validate_entry(self) -> Self:
        if _NPM_NAME.fullmatch(self.name) is None or _RANGE.fullmatch(self.version_range) is None:
            raise ValueError("npm root dependency identity or range is invalid.")
        if not self.version_range.strip():
            raise ValueError("npm root dependency range is empty.")
        return self


class NpmLockedPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    package_path: str = Field(min_length=1, max_length=MAX_PACKAGE_PATH_BYTES)
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=5, max_length=128)
    resolved_url: str = Field(min_length=1, max_length=2048)
    integrity: str = Field(pattern=r"^sha512-[A-Za-z0-9+/]{86}==$", max_length=96)

    @model_validator(mode="after")
    def validate_package(self) -> Self:
        if _NPM_NAME.fullmatch(self.name) is None or _SEMVER.fullmatch(self.version) is None:
            raise ValueError("npm package name or semantic version is invalid.")
        segments = self.package_path.split("/")
        if (
            "\\" in self.package_path
            or any(segment in {"", ".", ".."} for segment in segments)
            or "node_modules" not in segments
        ):
            raise ValueError("npm lock package path is invalid.")
        marker = len(segments) - 1 - segments[::-1].index("node_modules")
        tail = segments[marker + 1 :]
        path_name = (
            "/".join(tail[:2]) if tail and tail[0].startswith("@") else (tail[0] if tail else "")
        )
        if path_name != self.name or len(tail) != (2 if path_name.startswith("@") else 1):
            raise ValueError("npm lock package path does not match its package name.")
        _validate_registry_tarball_url(self.resolved_url)
        try:
            digest = base64.b64decode(self.integrity[7:], validate=True)
        except ValueError as exc:
            raise ValueError("npm package SHA-512 integrity is invalid.") from exc
        if len(digest) != 64:
            raise ValueError("npm package SHA-512 integrity has the wrong digest length.")
        return self


class ProcedureNpmDependencyLock(BaseModel):
    """Immutable review record for one imported procedure and Node runtime."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: int = Field(ge=1, le=1)
    skill_id: str
    skill_version: int = Field(ge=1, le=128)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lockfile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    distribution: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    node_version: str = Field(min_length=5, max_length=128)
    npm_version: str = Field(min_length=5, max_length=128)
    requirements: tuple[str, ...] = Field(default=(), max_length=128)
    root_dependencies: tuple[NpmRootDependency, ...] = Field(max_length=512)
    packages: tuple[NpmLockedPackage, ...] = Field(max_length=MAX_LOCKED_PACKAGES)

    @field_validator("requirements", mode="before")
    @classmethod
    def freeze_requirements(cls, values: object) -> object:
        return tuple(cast(list[str], values)) if isinstance(values, list) else values

    @field_validator("root_dependencies", mode="before")
    @classmethod
    def freeze_root_dependencies(cls, values: object) -> object:
        return tuple(cast(list[NpmRootDependency], values)) if isinstance(values, list) else values

    @field_validator("packages", mode="before")
    @classmethod
    def freeze_packages(cls, values: object) -> object:
        return tuple(cast(list[NpmLockedPackage], values)) if isinstance(values, list) else values

    @model_validator(mode="after")
    def validate_lock(self) -> Self:
        try:
            if str(uuid.UUID(self.skill_id)) != self.skill_id:
                raise ValueError("Skill ID must be a canonical UUID.")
        except ValueError as exc:
            raise ValueError("Skill ID must be a canonical UUID.") from exc
        if (
            _SEMVER.fullmatch(self.node_version.removeprefix("v")) is None
            or _SEMVER.fullmatch(self.npm_version) is None
        ):
            raise ValueError("Node and npm versions must be semantic versions.")
        paths = [item.package_path for item in self.packages]
        if len(paths) != len(set(paths)):
            raise ValueError("npm lock package paths must be unique.")
        identities = {(item.kind, item.name) for item in self.root_dependencies}
        if len(identities) != len(self.root_dependencies):
            raise ValueError("npm root dependency entries must be unique per dependency type.")
        for requirement in self.requirements:
            match = _NPM_SPEC.fullmatch(requirement)
            if match is None:
                raise ValueError("Imported npm dependency requirement is invalid.")
            if not any(
                item.name == match.group("name")
                and (match.group("range") is None or item.version_range == match.group("range"))
                for item in self.root_dependencies
            ):
                raise ValueError("npm lock root dependencies do not match imported requirements.")
        return self

    def environment_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def canonical_json(self) -> str:
        """Serialize the exact proposal with stable JSON object-key ordering."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class NpmVerifiedTarball(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    package_path: str
    name: str
    version: str
    integrity: str
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    archive_size_bytes: int = Field(gt=0, le=MAX_NPM_TARBALL_BYTES)
    member_count: int = Field(ge=1, le=MAX_NPM_TARBALL_MEMBERS)
    expanded_size_bytes: int = Field(ge=1, le=MAX_NPM_TARBALL_EXPANDED_BYTES)
    install_lifecycle_scripts: tuple[str, ...] = Field(max_length=4)

    @field_validator("install_lifecycle_scripts", mode="before")
    @classmethod
    def freeze_lifecycle_scripts(cls, values: object) -> object:
        return tuple(cast(list[str], values)) if isinstance(values, list) else values


class NpmDependencyLockRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    skill_id: str
    skill_version: int = Field(ge=1, le=128)
    environment_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lock: ProcedureNpmDependencyLock
    decision: Literal["pending_review", "approved", "rejected"]
    tarballs_present: bool
    verified_tarballs: tuple[NpmVerifiedTarball, ...] = Field(
        default=(), max_length=MAX_LOCKED_PACKAGES
    )
    created_at: str
    reviewed_at: str | None

    @field_validator("verified_tarballs", mode="before")
    @classmethod
    def freeze_verified_tarballs(cls, values: object) -> object:
        return tuple(cast(list[NpmVerifiedTarball], values)) if isinstance(values, list) else values

    @model_validator(mode="after")
    def validate_record_identity(self) -> Self:
        if (
            self.skill_id != self.lock.skill_id
            or self.skill_version != self.lock.skill_version
            or self.source_sha256 != self.lock.source_sha256
            or self.environment_digest != self.lock.environment_digest()
        ):
            raise ValueError("npm lock record identity does not match its proposal.")
        if self.tarballs_present and (
            len(self.verified_tarballs) != len(self.lock.packages)
            or tuple(item.package_path for item in self.verified_tarballs)
            != tuple(item.package_path for item in self.lock.packages)
        ):
            raise ValueError("npm lock record verification does not cover its exact package set.")
        if not self.tarballs_present and self.verified_tarballs:
            raise ValueError("npm lock record cannot list uncommitted tarball verifications.")
        return self


class NpmDependencyLockList(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    locks: tuple[NpmDependencyLockRecord, ...]

    @field_validator("locks", mode="before")
    @classmethod
    def freeze_locks(cls, values: object) -> object:
        return (
            tuple(cast(list[NpmDependencyLockRecord], values))
            if isinstance(values, list)
            else values
        )


class _BoundedTarReader:
    def __init__(self, source: Any, maximum: int) -> None:
        self._source = source
        self._maximum = maximum
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self._maximum - self._read
        requested = remaining + 1 if size < 0 else min(size, remaining + 1)
        data = self._source.read(requested)
        self._read += len(data)
        if self._read > self._maximum:
            raise ValueError("npm tarball decompressed stream exceeds its size limit.")
        return data


def parse_npm_package_lock(
    raw: bytes,
    *,
    skill_id: str,
    skill_version: int,
    source_sha256: str,
    package_manifest_sha256: str,
    distribution: str,
    node_version: str,
    npm_version: str,
    requirements: Iterable[str] = (),
) -> ProcedureNpmDependencyLock:
    """Parse a lockfile v3 proposal; reject links and non-registry package sources."""
    if not raw or len(raw) > MAX_LOCKFILE_BYTES:
        raise ValueError("npm package-lock.json is empty or exceeds its size limit.")
    try:
        parsed_json: object = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("npm package-lock.json is invalid JSON.") from exc
    if not isinstance(parsed_json, dict):
        raise TypeError("npm package-lock.json must be a JSON object.")
    document = cast(dict[str, Any], parsed_json)
    if document.get("lockfileVersion") != 3:
        raise ValueError("Only npm package-lock.json format version 3 is supported.")
    raw_packages = document.get("packages")
    if not isinstance(raw_packages, dict) or "" not in raw_packages:
        raise ValueError("npm package-lock.json has no root package record.")
    raw_packages = cast(dict[str, Any], raw_packages)
    if len(raw_packages) - 1 > MAX_LOCKED_PACKAGES:
        raise ValueError("npm package-lock.json exceeds the package limit.")
    root = raw_packages.get("")
    if not isinstance(root, dict):
        raise TypeError("npm package-lock.json root package record is invalid.")
    root = cast(dict[str, Any], root)
    root_dependencies: list[NpmRootDependency] = []
    for kind in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        raw_dependencies = root.get(kind, {})
        if not isinstance(raw_dependencies, dict):
            raise TypeError("npm package-lock.json root dependencies are invalid.")
        raw_dependencies = cast(dict[str, Any], raw_dependencies)
        for name, version_range in raw_dependencies.items():
            if not isinstance(version_range, str):
                raise TypeError("npm package-lock.json root dependency is malformed.")
            root_dependencies.append(
                NpmRootDependency(name=name, version_range=version_range or "*", kind=kind)
            )
    packages: list[NpmLockedPackage] = []
    for package_path, record in raw_packages.items():
        if package_path == "":
            continue
        if not isinstance(record, dict):
            raise TypeError("npm package-lock.json contains an invalid package entry.")
        record = cast(dict[str, Any], record)
        if record.get("link") is True or record.get("inBundle") is True:
            raise ValueError("Linked and bundled npm packages are not supported.")
        package_name = _package_name_from_lock_path(package_path)
        version = record.get("version")
        resolved = record.get("resolved")
        integrity = record.get("integrity")
        if (
            not isinstance(version, str)
            or not isinstance(resolved, str)
            or not isinstance(integrity, str)
        ):
            raise TypeError("npm lock package version, URL, or integrity is missing.")
        packages.append(
            NpmLockedPackage(
                package_path=package_path,
                name=package_name,
                version=version,
                resolved_url=resolved,
                integrity=integrity,
            )
        )
    _validate_npm_dependency_graph(root, raw_packages, root_dependencies, packages)
    return ProcedureNpmDependencyLock(
        schema_version=1,
        skill_id=skill_id,
        skill_version=skill_version,
        source_sha256=source_sha256,
        package_manifest_sha256=package_manifest_sha256,
        lockfile_sha256=hashlib.sha256(raw).hexdigest(),
        distribution=distribution,
        node_version=node_version,
        npm_version=npm_version,
        requirements=tuple(requirements),
        root_dependencies=tuple(root_dependencies),
        packages=tuple(packages),
    )


def _validate_npm_dependency_graph(
    root: dict[str, Any],
    raw_packages: dict[str, Any],
    root_dependencies: list[NpmRootDependency],
    packages: list[NpmLockedPackage],
) -> None:
    """Require every lock edge to resolve to a locked version satisfying npm SemVer."""
    package_by_path = {package.package_path: package for package in packages}
    reachable: set[str] = set()
    edges_seen = 0
    pending: list[tuple[str, dict[str, Any], bool]] = [("", root, True)]
    while pending:
        parent_path, record, is_root = pending.pop()
        if is_root:
            dependencies = _root_dependency_edges(root_dependencies, record)
        else:
            dependencies = _dependency_edges(record)
        for name, version_range, optional in dependencies:
            edges_seen += 1
            if edges_seen > MAX_NPM_DEPENDENCY_EDGES:
                raise ValueError("npm package-lock.json exceeds the dependency edge limit.")
            resolved_path = _resolve_npm_dependency_path(parent_path, name, package_by_path)
            if resolved_path is None:
                if optional:
                    continue
                raise ValueError(f"npm lock dependency {name!r} is missing from the package graph.")
            locked = package_by_path[resolved_path]
            _require_npm_version_satisfies(locked.version, version_range, name)
            if resolved_path in reachable:
                continue
            reachable.add(resolved_path)
            nested_record = raw_packages[resolved_path]
            if not isinstance(nested_record, dict):
                raise TypeError("npm package-lock.json contains an invalid package entry.")
            pending.append((resolved_path, cast(dict[str, Any], nested_record), False))
    if reachable != set(package_by_path):
        raise ValueError("npm package-lock.json contains packages outside the dependency graph.")


def _dependency_edges(record: dict[str, Any]) -> list[tuple[str, str, bool]]:
    declarations: dict[str, tuple[str, bool]] = {}
    for field, optional in (("dependencies", False), ("optionalDependencies", True)):
        values = record.get(field, {})
        if not isinstance(values, dict):
            raise TypeError(f"npm package-lock.json {field} are invalid.")
        for name, version_range in cast(dict[str, Any], values).items():
            if not isinstance(version_range, str):
                raise TypeError("npm package-lock.json dependency edge is malformed.")
            declarations[name] = (version_range or "*", optional)
    edges = [(name, range_, optional) for name, (range_, optional) in declarations.items()]

    peer_dependencies = record.get("peerDependencies", {})
    peer_metadata = record.get("peerDependenciesMeta", {})
    if not isinstance(peer_dependencies, dict) or not isinstance(peer_metadata, dict):
        raise TypeError("npm package-lock.json peer dependency metadata is invalid.")
    peer_values = cast(dict[str, Any], peer_dependencies)
    metadata_values = cast(dict[str, Any], peer_metadata)
    if set(metadata_values) - set(peer_values):
        raise ValueError("npm package-lock.json has metadata for an undeclared peer dependency.")
    for name, version_range in peer_values.items():
        if not isinstance(version_range, str):
            raise TypeError("npm package-lock.json peer dependency edge is malformed.")
        metadata = metadata_values.get(name, {})
        if not isinstance(metadata, dict):
            raise TypeError("npm package-lock.json peer metadata entry is invalid.")
        metadata = cast(dict[str, Any], metadata)
        is_optional: object = metadata.get("optional", False)
        if not isinstance(is_optional, bool):
            raise TypeError("npm package-lock.json peer optional flag is invalid.")
        edges.append((name, version_range or "*", is_optional))
    return edges


def _root_dependency_edges(
    root_dependencies: list[NpmRootDependency], root_record: dict[str, Any]
) -> list[tuple[str, str, bool]]:
    required = [
        (item.name, item.version_range, False)
        for item in root_dependencies
        if item.kind not in {"optionalDependencies", "peerDependencies"}
    ]
    optional_overrides: dict[str, tuple[str, bool]] = {}
    for dependency in root_dependencies:
        if dependency.kind == "optionalDependencies":
            optional_overrides[dependency.name] = (dependency.version_range, True)

    peer_metadata = root_record.get("peerDependenciesMeta", {})
    if not isinstance(peer_metadata, dict):
        raise TypeError("npm package-lock.json root peer metadata is invalid.")
    peer_values = cast(dict[str, Any], peer_metadata)
    peer_names = {item.name for item in root_dependencies if item.kind == "peerDependencies"}
    if set(peer_values) - peer_names:
        raise ValueError("npm package-lock.json root has metadata for an undeclared peer.")
    for dependency in root_dependencies:
        if dependency.kind != "peerDependencies":
            continue
        metadata = peer_values.get(dependency.name, {})
        if not isinstance(metadata, dict):
            raise TypeError("npm package-lock.json root peer metadata entry is invalid.")
        metadata = cast(dict[str, Any], metadata)
        peer_is_optional: object = metadata.get("optional", False)
        if not isinstance(peer_is_optional, bool):
            raise TypeError("npm package-lock.json root peer optional flag is invalid.")
        required.append((dependency.name, dependency.version_range, peer_is_optional))
    required = [edge for edge in required if edge[0] not in optional_overrides]
    return required + [
        (name, range_, is_optional) for name, (range_, is_optional) in optional_overrides.items()
    ]


def _resolve_npm_dependency_path(
    parent_path: str, name: str, package_by_path: dict[str, NpmLockedPackage]
) -> str | None:
    if len(name) > 256 or _NPM_NAME.fullmatch(name) is None:
        raise ValueError("npm package-lock.json dependency name is invalid.")
    ancestors: list[str] = []
    current = parent_path
    while current:
        ancestors.append(current)
        current, separator, _ = current.rpartition("/node_modules/")
        if not separator:
            break
    for ancestor in (*ancestors, ""):
        candidate = f"{ancestor}/node_modules/{name}" if ancestor else f"node_modules/{name}"
        if candidate in package_by_path:
            return candidate
    return None


def _require_npm_version_satisfies(version: str, version_range: str, package_name: str) -> None:
    if _NODE_SEMVER_VALID(version, False) is None:
        raise ValueError(f"npm package {package_name!r} has an invalid semantic version.")
    if (
        len(version_range) > 128
        or not version_range.strip()
        or _RANGE.fullmatch(version_range) is None
    ):
        raise ValueError(f"npm package {package_name!r} has an invalid semantic version range.")
    if _NODE_SEMVER_VALID_RANGE(version_range, False) is None:
        raise ValueError(f"npm package {package_name!r} has an invalid semantic version range.")
    if not _NODE_SEMVER_SATISFIES(version, version_range):
        raise ValueError(f"locked npm package {package_name!r} does not satisfy {version_range!r}.")


def verify_npm_package_manifest(raw: bytes, lock: ProcedureNpmDependencyLock) -> str:
    """Match package.json dependency declarations to the root entry in the reviewed lock."""
    if not raw or len(raw) > MAX_PACKAGE_MANIFEST_BYTES:
        raise ValueError("npm package.json is empty or exceeds its size limit.")
    try:
        parsed_json: object = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("npm package.json is invalid JSON.") from exc
    if not isinstance(parsed_json, dict):
        raise TypeError("npm package.json must be a JSON object.")
    manifest = cast(dict[str, Any], parsed_json)
    declared: list[NpmRootDependency] = []
    for kind in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        values = manifest.get(kind, {})
        if not isinstance(values, dict):
            raise TypeError("npm package.json dependencies are invalid.")
        for name, version_range in cast(dict[str, Any], values).items():
            if not isinstance(version_range, str):
                raise TypeError("npm package.json dependency range is invalid.")
            declared.append(
                NpmRootDependency(name=name, version_range=version_range or "*", kind=kind)
            )
    expected = sorted((item.kind, item.name, item.version_range) for item in lock.root_dependencies)
    actual = sorted((item.kind, item.name, item.version_range) for item in declared)
    if expected != actual:
        raise ValueError("npm package.json dependencies do not match package-lock.json.")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != lock.package_manifest_sha256:
        raise ValueError("npm package.json bytes do not match the bound procedure resource.")
    return digest


def verify_npm_tarball(locked: NpmLockedPackage, archive: bytes) -> NpmVerifiedTarball:
    """Verify SRI and inspect a bounded npm tarball without writing or executing its files."""
    if not archive or len(archive) > MAX_NPM_TARBALL_BYTES:
        raise ValueError("npm tarball is empty or exceeds its size limit.")
    expected_integrity = locked.integrity[7:]
    actual_integrity = base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii")
    if not hmac.compare_digest(actual_integrity, expected_integrity):
        raise ValueError("npm tarball bytes do not match the lockfile integrity value.")

    member_count = 0
    expanded_size = 0
    seen_paths: set[str] = set()
    package_metadata: dict[str, Any] | None = None
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(archive), mode="rb") as compressed:
            bounded = _BoundedTarReader(compressed, MAX_NPM_TARBALL_STREAM_BYTES)
            with cast(Any, tarfile.open(fileobj=cast(Any, bounded), mode="r|")) as tar:
                for member in tar:
                    member_count += 1
                    if member_count > MAX_NPM_TARBALL_MEMBERS:
                        raise ValueError("npm tarball contains too many entries.")
                    name = member.name
                    if member.isdir() and name.endswith("/"):
                        name = name[:-1]
                    if (
                        not name
                        or "\x00" in name
                        or "\\" in name
                        or name.startswith("/")
                        or any(part in {"", ".", ".."} for part in name.split("/"))
                        or name.split("/", 1)[0] != "package"
                        or len(name.encode("utf-8", errors="strict")) > MAX_PACKAGE_PATH_BYTES
                    ):
                        raise ValueError("npm tarball contains an unsafe package path.")
                    if name in seen_paths:
                        raise ValueError("npm tarball contains duplicate paths.")
                    seen_paths.add(name)
                    if member.isdir():
                        if member.size != 0:
                            raise ValueError("npm tarball directory entry has a nonzero size.")
                        continue
                    if not member.isfile() or getattr(member, "sparse", None):
                        raise ValueError("npm tarball contains a link or special filesystem entry.")
                    if member.size < 0 or member.size > MAX_NPM_TARBALL_MEMBER_BYTES:
                        raise ValueError("npm tarball file exceeds its size limit.")
                    expanded_size += member.size
                    if expanded_size > MAX_NPM_TARBALL_EXPANDED_BYTES:
                        raise ValueError("npm tarball exceeds its expanded size limit.")
                    if name != "package/package.json":
                        continue
                    if member.size > MAX_NPM_TARBALL_PACKAGE_JSON_BYTES:
                        raise ValueError("npm package metadata exceeds its size limit.")
                    stream = tar.extractfile(member)
                    if stream is None:
                        raise ValueError("npm package metadata could not be read.")
                    raw_metadata = stream.read(MAX_NPM_TARBALL_PACKAGE_JSON_BYTES + 1)
                    if (
                        len(raw_metadata) != member.size
                        or len(raw_metadata) > MAX_NPM_TARBALL_PACKAGE_JSON_BYTES
                    ):
                        raise ValueError("npm package metadata size is invalid.")
                    try:
                        parsed: object = json.loads(raw_metadata, object_pairs_hook=_unique_object)
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                        raise ValueError("npm package metadata is invalid JSON.") from exc
                    if not isinstance(parsed, dict):
                        raise TypeError("npm package metadata must be a JSON object.")
                    package_metadata = cast(dict[str, Any], parsed)
    except (tarfile.TarError, OSError, EOFError, UnicodeError) as exc:
        raise ValueError("npm tarball archive is malformed.") from exc

    if package_metadata is None:
        raise ValueError("npm tarball has no package/package.json entry.")
    if (
        package_metadata.get("name") != locked.name
        or package_metadata.get("version") != locked.version
    ):
        raise ValueError("npm tarball package identity does not match the lockfile.")
    scripts_value = package_metadata.get("scripts", {})
    if not isinstance(scripts_value, dict):
        raise TypeError("npm package scripts metadata is invalid.")
    scripts = cast(dict[str, Any], scripts_value)
    lifecycle = ("preinstall", "install", "postinstall", "prepare")
    script_names: list[str] = []
    for script_name in lifecycle:
        script_value = scripts.get(script_name)
        if script_value is not None:
            if not isinstance(script_value, str):
                raise ValueError("npm package lifecycle script metadata is invalid.")
            script_names.append(script_name)
    return NpmVerifiedTarball(
        package_path=locked.package_path,
        name=locked.name,
        version=locked.version,
        integrity=locked.integrity,
        archive_sha256=hashlib.sha256(archive).hexdigest(),
        archive_size_bytes=len(archive),
        member_count=member_count,
        expanded_size_bytes=expanded_size,
        install_lifecycle_scripts=tuple(script_names),
    )


def _package_name_from_lock_path(package_path: str) -> str:
    segments = package_path.split("/")
    if "node_modules" not in segments:
        raise ValueError("npm package path is outside node_modules.")
    marker = len(segments) - 1 - segments[::-1].index("node_modules")
    tail = segments[marker + 1 :]
    if not tail:
        raise ValueError("npm package path has no package name.")
    return "/".join(tail[:2]) if tail[0].startswith("@") and len(tail) >= 2 else tail[0]


def _validate_registry_tarball_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "registry.npmjs.org"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or "\\" in parsed.path
        or any(segment in {".", ".."} for segment in unquote(parsed.path).split("/"))
        or not parsed.path.endswith(".tgz")
    ):
        raise ValueError("npm package tarball must use the fixed HTTPS npm registry host.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("npm package-lock.json contains duplicate object keys.")
        result[key] = value
    return result

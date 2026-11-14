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
from collections.abc import Iterable
from typing import Any, Self, cast
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_LOCKFILE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_MANIFEST_BYTES = 1024 * 1024
MAX_LOCKED_PACKAGES = 512
MAX_PACKAGE_PATH_BYTES = 1024
MAX_NPM_TARBALL_BYTES = 64 * 1024 * 1024
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
        payload = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


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

"""Immutable, target-specific lock records for imported procedure dependencies."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import uuid
import zipfile
from collections.abc import Mapping
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self, cast

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.tags import parse_tag
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

MAX_DEPENDENCIES = 64
MAX_WHEELS = 256
MAX_REQUIREMENTS_PER_WHEEL = 256
MAX_LOCKED_REQUIREMENTS = 4096
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_ENVIRONMENT_BYTES = 256 * 1024 * 1024
MAX_WHEEL_METADATA_BYTES = 1024 * 1024
MAX_WHEEL_DESCRIPTION_BYTES = 64 * 1024
MAX_WHEEL_MEMBERS = 100_000
PYTHON_ENVIRONMENT_KEYS = frozenset(
    {
        "implementation_name",
        "implementation_version",
        "os_name",
        "platform_machine",
        "platform_python_implementation",
        "platform_release",
        "platform_system",
        "platform_version",
        "python_full_version",
        "python_version",
        "sys_platform",
    }
)


class PythonDependencyTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    distribution: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    environment: dict[str, str]
    compatible_wheel_tags: tuple[str, ...] = Field(min_length=1, max_length=256)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, environment: dict[str, str]) -> dict[str, str]:
        if frozenset(environment) != PYTHON_ENVIRONMENT_KEYS or any(
            not item or len(item) > 256 for item in environment.values()
        ):
            raise ValueError("Target Python environment is incomplete or out of bounds.")
        return dict(sorted(environment.items()))

    @model_validator(mode="after")
    def freeze_environment(self) -> Self:
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))
        return self

    @field_serializer("environment", when_used="json")
    def serialize_environment(self, value: dict[str, str]) -> dict[str, str]:
        return dict(value)

    @field_validator("compatible_wheel_tags", mode="before")
    @classmethod
    def freeze_compatible_wheel_tags(cls, values: object) -> object:
        return tuple(cast(list[str], values)) if isinstance(values, list) else values

    @field_validator("compatible_wheel_tags")
    @classmethod
    def validate_compatible_wheel_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 128 for value in values):
            raise ValueError("Target wheel compatibility tags are invalid.")
        if len(set(values)) != len(values):
            raise ValueError("Target wheel compatibility tags must be unique.")
        try:
            for value in values:
                parse_tag(value)
        except ValueError as exc:
            raise ValueError("Target wheel compatibility tag is invalid.") from exc
        # sys_tags() is ordered from most to least preferred. Keep that order for
        # resolver candidate selection; canonical_json sorts it explicitly so
        # existing lock identities remain stable.
        return values


class LockedPythonWheel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    filename: str = Field(min_length=1, max_length=255)
    project_name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=MAX_WHEEL_BYTES)
    requires_dist: tuple[str, ...] = Field(default=(), max_length=MAX_REQUIREMENTS_PER_WHEEL)

    @field_validator("requires_dist", mode="before")
    @classmethod
    def freeze_requires_dist(cls, values: object) -> object:
        return tuple(cast(list[str], values)) if isinstance(values, list) else values

    @field_validator("requires_dist")
    @classmethod
    def normalize_requires_dist(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            if not value or len(value) > 512:
                raise ValueError("Wheel dependency metadata is invalid or out of bounds.")
            try:
                requirement = Requirement(value)
            except InvalidRequirement as exc:
                raise ValueError(
                    "Wheel dependency metadata contains an invalid requirement."
                ) from exc
            if requirement.url is not None or requirement.extras:
                raise ValueError("Wheel dependencies cannot use direct URLs or extras.")
            normalized.append(str(requirement))
        return tuple(sorted(normalized, key=str.casefold))

    @model_validator(mode="after")
    def validate_wheel_identity(self) -> Self:
        if (
            self.filename in {".", ".."}
            or "/" in self.filename
            or "\\" in self.filename
            or not self.filename.endswith(".whl")
        ):
            raise ValueError("Wheel filename must be a basename ending in .whl.")
        try:
            parsed_name, parsed_version, _build, _tags = parse_wheel_filename(self.filename)
            version = Version(self.version)
        except (InvalidWheelFilename, InvalidVersion) as exc:
            raise ValueError("Wheel filename or version is invalid.") from exc
        if (
            canonicalize_name(self.project_name) != self.project_name
            or parsed_name != self.project_name
            or parsed_version != version
        ):
            raise ValueError("Wheel filename, project name, and version must agree.")
        return self


class ProcedurePythonDependencyLock(BaseModel):
    """Owner-reviewable lock for one immutable skill version and WSL interpreter."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: int = Field(ge=1, le=1)
    skill_id: str
    skill_version: int = Field(ge=1, le=128)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: PythonDependencyTarget
    requirements: tuple[str, ...] = Field(min_length=1, max_length=MAX_DEPENDENCIES)
    wheels: tuple[LockedPythonWheel, ...] = Field(max_length=MAX_WHEELS)
    index_url: str = Field(default="https://pypi.org/simple", pattern=r"^https://pypi\.org/simple$")

    @field_validator("skill_id")
    @classmethod
    def validate_skill_id(cls, value: str) -> str:
        try:
            parsed = uuid.UUID(value)
        except ValueError as exc:
            raise ValueError("Skill ID must be a canonical UUID.") from exc
        if str(parsed) != value:
            raise ValueError("Skill ID must be a canonical UUID.")
        return value

    @field_validator("requirements", mode="before")
    @classmethod
    def freeze_requirements(cls, values: object) -> object:
        return tuple(cast(list[str], values)) if isinstance(values, list) else values

    @field_validator("requirements")
    @classmethod
    def validate_requirements(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            if not value or len(value) > 256:
                raise ValueError("Python dependency requirement is invalid.")
            try:
                requirement = Requirement(value)
            except InvalidRequirement as exc:
                raise ValueError("Python dependency requirement is invalid.") from exc
            if requirement.url is not None:
                raise ValueError("Direct dependency URLs are not supported.")
            if requirement.extras:
                raise ValueError("Python dependency extras are not supported.")
            normalized.append(str(requirement))
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("Python dependency requirements must be unique.")
        return tuple(sorted(normalized, key=str.casefold))

    @field_validator("wheels", mode="before")
    @classmethod
    def freeze_wheels(cls, values: object) -> object:
        return tuple(cast(list[LockedPythonWheel], values)) if isinstance(values, list) else values

    @model_validator(mode="after")
    def validate_wheel_set(self) -> Self:
        names = [wheel.project_name for wheel in self.wheels]
        if len(names) != len(set(names)):
            raise ValueError("A dependency lock may include one wheel per project.")
        if sum(len(wheel.requires_dist) for wheel in self.wheels) > MAX_LOCKED_REQUIREMENTS:
            raise ValueError("Wheel dependency metadata exceeds the lock graph limit.")
        if sum(wheel.size_bytes for wheel in self.wheels) > MAX_ENVIRONMENT_BYTES:
            raise ValueError("Locked wheels exceed the per-environment size limit.")
        wheel_by_name = {wheel.project_name: wheel for wheel in self.wheels}
        compatible_tags = {
            tag for value in self.target.compatible_wheel_tags for tag in parse_tag(value)
        }
        for wheel in self.wheels:
            _name, _version, _build, wheel_tags = parse_wheel_filename(wheel.filename)
            if not compatible_tags.intersection(wheel_tags):
                raise ValueError("A locked wheel is incompatible with the target Python ABI.")
        marker_environment = cast(dict[str, str], default_environment())
        marker_environment.update(self.target.environment)
        pending = list(self.requirements)
        reachable: set[str] = set()
        while pending:
            value = pending.pop()
            requirement = Requirement(value)
            if requirement.marker is not None and not requirement.marker.evaluate(
                marker_environment
            ):
                continue
            name = canonicalize_name(requirement.name)
            wheel = wheel_by_name.get(name)
            if wheel is None:
                raise ValueError("Every applicable wheel dependency needs a locked wheel.")
            if requirement.specifier and Version(wheel.version) not in requirement.specifier:
                raise ValueError("A locked wheel does not satisfy its dependency requirement.")
            if name not in reachable:
                reachable.add(name)
                pending.extend(wheel.requires_dist)
        if reachable != set(wheel_by_name):
            raise ValueError(
                "A dependency lock cannot include wheels outside its requirement graph."
            )
        return self

    def environment_digest(self) -> str:
        """Hash canonical lock content; timestamps and input ordering cannot change identity."""
        encoded = self.canonical_json().encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    def canonical_json(self) -> str:
        """Serialize normalized lock content independently of requirement/wheel ordering."""
        payload = self.model_dump(mode="json")
        payload["target"]["compatible_wheel_tags"] = sorted(self.target.compatible_wheel_tags)
        payload["requirements"] = sorted(self.requirements, key=str.casefold)
        payload["wheels"] = sorted(
            (wheel.model_dump(mode="json") for wheel in self.wheels),
            key=lambda item: item["project_name"],
        )
        for wheel in payload["wheels"]:
            if not wheel["requires_dist"]:
                # Preserve the v1 canonical identity for locks saved before metadata was recorded.
                wheel.pop("requires_dist")
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class PythonDependencyLockRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    skill_id: str
    skill_version: int
    environment_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lock: ProcedurePythonDependencyLock
    decision: Literal["pending_review", "approved", "rejected"]
    wheelhouse_present: bool
    created_at: str
    reviewed_at: str | None
    environment_installed: bool = False
    installed_tree_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    installed_bytes: int | None = Field(default=None, ge=1, le=671088640)
    installed_file_count: int | None = Field(default=None, ge=1, le=20000)
    installed_at: str | None = None


class PythonDependencyLockList(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    locks: tuple[PythonDependencyLockRecord, ...]


def verify_locked_wheel_bytes(
    lock: ProcedurePythonDependencyLock, artifacts: Mapping[str, bytes]
) -> dict[str, bytes]:
    """Verify exact wheelhouse bytes against the reviewed lock before installation."""
    expected = {wheel.filename: wheel for wheel in lock.wheels}
    if set(artifacts) != set(expected):
        raise ValueError("Wheelhouse files must exactly match the reviewed dependency lock.")
    for filename in expected:
        _verify_wheel_source(lock, filename, artifacts[filename])
    return dict(artifacts)


def verify_locked_wheel_paths(
    lock: ProcedurePythonDependencyLock, artifacts: Mapping[str, Path]
) -> dict[str, Path]:
    """Verify an exact wheelhouse by path with bounded memory use."""
    expected = {wheel.filename: wheel for wheel in lock.wheels}
    if set(artifacts) != set(expected):
        raise ValueError("Wheelhouse files must exactly match the reviewed dependency lock.")
    for filename, path in artifacts.items():
        if path.is_symlink() or path.is_junction() or not path.is_file():
            raise ValueError("A wheel artifact must be a regular file.")
        _verify_wheel_source(lock, filename, path)
    return dict(artifacts)


def _verify_wheel_source(
    lock: ProcedurePythonDependencyLock, filename: str, source: bytes | Path
) -> None:
    record = next((item for item in lock.wheels if item.filename == filename), None)
    if record is None:
        raise ValueError("Wheelhouse contains a file outside the reviewed dependency lock.")
    if isinstance(source, bytes):
        if len(source) != record.size_bytes:
            raise ValueError("A wheel's byte size does not match the reviewed lock.")
        if hashlib.sha256(source).hexdigest() != record.sha256:
            raise ValueError("A wheel's SHA-256 does not match the reviewed lock.")
        archive_source: bytes | Path = source
    else:
        info = source.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size != record.size_bytes:
            raise ValueError("A wheel's byte size does not match the reviewed lock.")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != record.sha256:
            raise ValueError("A wheel's SHA-256 does not match the reviewed lock.")
        archive_source = source
    try:
        with zipfile.ZipFile(
            io.BytesIO(archive_source) if isinstance(archive_source, bytes) else archive_source
        ) as archive:
            members = archive.infolist()
            if len(members) > MAX_WHEEL_MEMBERS:
                raise ValueError("A wheel contains too many archive members.")
            archive_names: set[str] = set()
            metadata_members: list[zipfile.ZipInfo] = []
            wheel_description_members: list[zipfile.ZipInfo] = []
            for member in members:
                name = member.filename
                if (
                    not name
                    or name.startswith("/")
                    or "\\" in name
                    or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
                    or name in archive_names
                    or member.flag_bits & 0x1
                    or stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK
                ):
                    raise ValueError("A wheel contains an unsafe or duplicate archive path.")
                archive_names.add(name)
                if name.endswith(".dist-info/METADATA"):
                    metadata_members.append(member)
                if name.endswith(".dist-info/WHEEL"):
                    wheel_description_members.append(member)
            if len(metadata_members) != 1:
                raise ValueError("A wheel must contain exactly one dist-info METADATA file.")
            if len(wheel_description_members) != 1:
                raise ValueError("A wheel must contain exactly one dist-info WHEEL file.")
            metadata_member = metadata_members[0]
            wheel_description_member = wheel_description_members[0]
            if metadata_member.file_size > MAX_WHEEL_METADATA_BYTES:
                raise ValueError("Wheel METADATA exceeds its size limit.")
            if wheel_description_member.file_size > MAX_WHEEL_DESCRIPTION_BYTES:
                raise ValueError("Wheel WHEEL metadata exceeds its size limit.")
            expected_dist_info = (
                f"{record.project_name.replace('-', '_')}-"
                f"{record.version.replace('-', '_')}.dist-info/METADATA"
            )
            if metadata_member.filename != expected_dist_info:
                raise ValueError("Wheel dist-info path does not match the reviewed identity.")
            if wheel_description_member.filename != expected_dist_info.replace("METADATA", "WHEEL"):
                raise ValueError("Wheel WHEEL path does not match the reviewed identity.")
            metadata_bytes = archive.read(metadata_member)
            wheel_description_bytes = archive.read(wheel_description_member)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("A locked wheel is not a readable wheel archive.") from exc

    message = BytesParser(policy=policy.default).parsebytes(metadata_bytes)
    if message.defects:
        raise ValueError("Wheel METADATA is malformed.")
    name_headers = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if len(name_headers) != 1 or len(versions) != 1:
        raise ValueError("Wheel METADATA must declare exactly one Name and Version.")
    try:
        metadata_version = Version(str(versions[0]))
        requirements = tuple(
            sorted(
                (str(Requirement(value)) for value in message.get_all("Requires-Dist", [])),
                key=str.casefold,
            )
        )
    except (InvalidRequirement, InvalidVersion) as exc:
        raise ValueError("Wheel METADATA contains an invalid dependency declaration.") from exc
    if canonicalize_name(str(name_headers[0])) != record.project_name:
        raise ValueError("Wheel METADATA project name does not match the reviewed lock.")
    if metadata_version != Version(record.version):
        raise ValueError("Wheel METADATA version does not match the reviewed lock.")
    if requirements != record.requires_dist:
        raise ValueError("Wheel METADATA dependencies do not match the reviewed lock.")
    wheel_description = BytesParser(policy=policy.default).parsebytes(wheel_description_bytes)
    if wheel_description.defects:
        raise ValueError("Wheel WHEEL metadata is malformed.")
    wheel_versions = wheel_description.get_all("Wheel-Version", [])
    actual_tags = wheel_description.get_all("Tag", [])
    if len(wheel_versions) != 1 or not re.fullmatch(r"1\.\d+", str(wheel_versions[0])):
        raise ValueError("Wheel WHEEL metadata has an unsupported format version.")
    try:
        _name, _version, _build, filename_tags = parse_wheel_filename(record.filename)
        declared_tags = {tag for value in actual_tags for tag in parse_tag(str(value))}
    except (InvalidWheelFilename, ValueError) as exc:
        raise ValueError("Wheel WHEEL metadata contains invalid compatibility tags.") from exc
    if not actual_tags or declared_tags != set(filename_tags):
        raise ValueError("Wheel WHEEL tags do not match its reviewed filename.")

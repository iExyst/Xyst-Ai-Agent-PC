"""Immutable, target-specific lock records for imported procedure dependencies."""

from __future__ import annotations

import hashlib
import json
import uuid
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
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_ENVIRONMENT_BYTES = 256 * 1024 * 1024
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
        return tuple(sorted(values))


class LockedPythonWheel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    filename: str = Field(min_length=1, max_length=255)
    project_name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=MAX_WHEEL_BYTES)

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
        for value in self.requirements:
            requirement = Requirement(value)
            if requirement.marker is not None and not requirement.marker.evaluate(
                marker_environment
            ):
                continue
            wheel = wheel_by_name.get(canonicalize_name(requirement.name))
            if wheel is None:
                raise ValueError("Every applicable direct requirement needs a locked wheel.")
            if requirement.specifier and Version(wheel.version) not in requirement.specifier:
                raise ValueError("A locked wheel does not satisfy its direct requirement.")
        return self

    def environment_digest(self) -> str:
        """Hash canonical lock content; timestamps and input ordering cannot change identity."""
        encoded = self.canonical_json().encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    def canonical_json(self) -> str:
        """Serialize normalized lock content independently of requirement/wheel ordering."""
        payload = self.model_dump(mode="json")
        payload["requirements"] = sorted(self.requirements, key=str.casefold)
        payload["wheels"] = sorted(
            (wheel.model_dump(mode="json") for wheel in self.wheels),
            key=lambda item: item["project_name"],
        )
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class PythonDependencyLockRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    skill_id: str
    skill_version: int
    environment_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lock: ProcedurePythonDependencyLock
    decision: Literal["pending_review", "approved", "rejected"]
    created_at: str
    reviewed_at: str | None


class PythonDependencyLockList(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    locks: tuple[PythonDependencyLockRecord, ...]

"""Bounded, read-only discovery for Agent Plugins and Hermes skill directories."""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata as importlib_metadata
import io
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from urllib.parse import unquote, urlsplit

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field

from xaa.storage.skills import (
    MAX_PROCEDURE_TEXT_BYTES,
    SUPPORTED_PROCEDURE_BINARY_MEDIA_TYPES,
    is_text_asset_media_type,
)

MAX_PACKAGES = 128
MAX_SKILLS = 128
MAX_SERVERS = 64
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SKILL_RESOURCE_COUNT = 32
MAX_SKILL_RESOURCE_BYTES = 64 * 1024
MAX_SKILL_RESOURCE_TOTAL_BYTES = 1024 * 1024
MAX_INSTALL_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_INSTALL_FILES = 1024
MAX_INSTALL_FILE_BYTES = 2 * 1024 * 1024
MAX_INSTALL_TOTAL_BYTES = 16 * 1024 * 1024
MAX_PLUGIN_FINGERPRINT_ENTRIES = 1024
MAX_PLUGIN_FINGERPRINT_FILE_BYTES = 2 * 1024 * 1024
MAX_PLUGIN_FINGERPRINT_TOTAL_BYTES = 16 * 1024 * 1024
MAX_READINESS_COMMAND_CHECKS = 256
MAX_READINESS_DEPENDENCY_CHECKS = 128
_PYTHON_DEPENDENCY_MANAGERS = {"python", "python3", "pip", "pip3"}
_NPM_PACKAGE_SPEC = re.compile(
    r"^(?P<name>(?:@[A-Za-z0-9][A-Za-z0-9._-]{0,127}/)?[A-Za-z0-9][A-Za-z0-9._-]{0,127})"
    r"(?:@(?P<range>[A-Za-z0-9.*+^~<>=| -]{1,128}))?$"
)
_NPM_PACKAGE_NAME = re.compile(
    r"^(?:@[A-Za-z0-9][A-Za-z0-9._-]{0,127}/)?[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_SCRIPT_MEDIA_TYPES = {
    ".cjs": "application/javascript",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
}
_SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
PLUGIN_SCHEMA_V1 = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_V1 = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
_PLUGIN_NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_SKILL_NAME = re.compile(r"^(?!.*--)[a-z0-9]+(?:-[a-z0-9]+)*$")
_CATEGORY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DEPENDENCY_SPEC = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]{0,127})"
    r"(?:\[[A-Za-z0-9._,-]{1,128}\])?"
    r"(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]{1,128}"
    r"(?:\s*,\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]{1,128}){0,15})?"
    r"(?:\s*;[^\r\n]{1,128})?\s*$"
)
_HEADER = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_LOCAL_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
_PLACEHOLDER_LINK = re.compile(r"[{}<>]")
_PLACEHOLDER_DESTINATIONS = {"link", "url"}


def _markdown_links(markdown: str) -> list[str]:
    """Return ordinary Markdown destinations, excluding fenced/inline code examples."""
    without_fences = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", markdown)
    without_inline_code = re.sub(r"(?<!`)`[^`\n]*`(?!`)", "", without_fences)
    return _LOCAL_LINK.findall(without_inline_code)


class PluginSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    path: str
    description: str
    category: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    declared_requirements: dict[str, Any] = Field(default_factory=dict)
    requirement_readiness: dict[str, Any] = Field(default_factory=dict)


class PluginServer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    transport: Literal["stdio", "streamable_http"]
    command_name: str | None = None
    argument_count: int = 0
    environment_names: list[str] = []
    endpoint: str | None = None


class PluginSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    version: str | None
    description: str | None
    path: str
    skills: list[PluginSkill]
    servers: list[PluginServer]


class PluginDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    message: str


class PluginDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    root: str
    packages: list[PluginSummary]
    diagnostics: list[PluginDiagnostic]


class PluginInstallResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    root: str
    package: PluginSummary


class PluginRemovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    removed: bool
    package_name: str


class PluginDataListing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    root: str
    package_names: list[str]


class PluginDataRemovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    removed: bool
    package_name: str


class PluginSkillImport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_format: Literal["agent_plugins_v1", "hermes_skill"]
    package_name: str
    package_version: str
    skill_name: str
    content: str
    description: str
    category: str | None = None
    allowed_tools: list[str]
    declared_requirements: dict[str, Any]
    content_sha256: str
    manifest_sha256: str
    assets: list[dict[str, str]]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("manifest contains duplicate keys")
        result[key] = value
    return result


def _bounded_names(value: Any, *, env: bool = False) -> list[str]:
    values: list[Any]
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, dict)):
        values = list(cast(Iterable[Any], value))[:64]
    else:
        return []
    pattern = _ENV if env else re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
    result: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        if not item.strip():
            continue
        candidate = (
            item
            if env
            else PurePosixPath(item.strip().split(maxsplit=1)[0].replace("\\", "/")).name
        )
        if pattern.fullmatch(candidate):
            result.add(candidate)
    return sorted(result)[:64]


def _declared_requirements(values: dict[str, Any]) -> dict[str, Any]:
    raw_prerequisites = values.get("prerequisites")
    prerequisites = (
        cast(dict[str, Any], raw_prerequisites) if isinstance(raw_prerequisites, dict) else {}
    )
    dependencies = values.get("dependencies")
    raw_metadata = values.get("metadata")
    metadata = cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
    raw_hermes = metadata.get("hermes")
    hermes = cast(dict[str, Any], raw_hermes) if isinstance(raw_hermes, dict) else {}
    dependency_names: set[str] = set()
    dependency_managers: set[str] = set()
    unparsed_dependency_count = 0
    dependency_items: list[tuple[str | None, Any]] = []
    dependency_items_truncated = False
    if isinstance(dependencies, (list, tuple)):
        all_dependency_items = list(cast(Iterable[Any], dependencies))
        dependency_items = [(None, item) for item in all_dependency_items[:128]]
        dependency_items_truncated = len(all_dependency_items) > 128
    elif isinstance(dependencies, dict):
        raw_managers = list(cast(dict[Any, Any], dependencies).items())
        dependency_items_truncated = len(raw_managers) > 64
        for manager, package_items in raw_managers[:64]:
            normalized_manager = (
                manager.lower()
                if isinstance(manager, str)
                and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,31}", manager)
                else None
            )
            if normalized_manager is not None:
                dependency_managers.add(normalized_manager)
            remaining = 128 - len(dependency_items)
            if isinstance(package_items, str):
                all_package_items = [package_items]
            elif isinstance(package_items, (list, tuple)):
                all_package_items = list(cast(Iterable[Any], package_items))
            else:
                all_package_items = [package_items]
            dependency_items.extend(
                (normalized_manager, item) for item in all_package_items[:remaining]
            )
            dependency_items_truncated = (
                dependency_items_truncated or len(all_package_items) > remaining
            )
            if len(dependency_items) >= 128:
                dependency_items_truncated = True
                break
    elif isinstance(dependencies, str):
        dependency_items = [(None, dependencies)]
    dependency_count = min(
        128,
        len(dependency_items) + int(dependency_items_truncated and len(dependency_items) < 128),
    )
    python_dependency_specs: list[str] = []
    npm_dependency_specs: list[str] = []
    unverified_dependency_packages: set[tuple[str, str]] = set()
    unsupported_dependency_count = 0
    for manager, item in dependency_items:
        if not isinstance(item, str) or len(item) > 256:
            unparsed_dependency_count += 1
            continue
        if manager == "npm":
            npm_match = _NPM_PACKAGE_SPEC.fullmatch(item.strip())
            if (
                npm_match is None
                or "  " in item
                or item.startswith(("-", ".", "@/"))
                or npm_match.group("range") is not None
                and not npm_match.group("range").strip()
            ):
                unparsed_dependency_count += 1
                continue
            npm_name = npm_match.group("name")
            dependency_names.add(npm_name)
            npm_dependency_specs.append(item.strip())
            unsupported_dependency_count += 1
            unverified_dependency_packages.add((manager, npm_name))
            continue
        match = _DEPENDENCY_SPEC.fullmatch(item)
        if match is None or "://" in item or item.startswith(("git+", "hg+", "svn+")):
            unparsed_dependency_count += 1
            continue
        dependency_names.add(match.group(1))
        if manager in _PYTHON_DEPENDENCY_MANAGERS:
            python_dependency_specs.append(item.strip())
        else:
            unsupported_dependency_count += 1
            unverified_dependency_packages.add((manager or "unspecified", match.group(1)))
    toolsets = _bounded_names(hermes.get("requires_toolsets"))
    tools = _bounded_names(hermes.get("requires_tools"))
    fallback_toolsets = _bounded_names(hermes.get("fallback_for_toolsets"))
    fallback_tools = _bounded_names(hermes.get("fallback_for_tools"))
    commands = _bounded_names(prerequisites.get("commands"))
    commands.extend(
        command
        for command in _bounded_names(values.get("required_commands"))
        if command not in commands
    )
    environment_names = _bounded_names(values.get("required_environment_variables"), env=True)
    return {
        "platforms": _bounded_names(values.get("platforms")),
        "hermes_toolsets": toolsets,
        "hermes_required_tools": tools,
        "hermes_fallback_toolsets": fallback_toolsets,
        "hermes_fallback_tools": fallback_tools,
        "required_command_names": commands,
        "required_environment_names": environment_names,
        "prerequisite_fields": sorted(str(key) for key in prerequisites)[:64],
        "dependencies_declared": dependencies is not None,
        "dependency_count": min(dependency_count, 128),
        "dependency_names": sorted(dependency_names)[:128],
        "python_dependency_specs": python_dependency_specs[:128],
        "npm_dependency_specs": npm_dependency_specs[:128],
        "unverified_dependency_packages": [
            {"manager": manager, "name": name}
            for manager, name in sorted(unverified_dependency_packages)[:128]
        ],
        "dependency_names_truncated": dependency_count > 128 or dependency_items_truncated,
        "dependency_managers": sorted(dependency_managers)[:64],
        "unparsed_dependency_count": min(unparsed_dependency_count, 128),
        "unsupported_dependency_count": min(unsupported_dependency_count, 128),
        "declared_fields": sorted(
            key
            for key in (
                "dependencies",
                "prerequisites",
                "required_commands",
                "required_environment_variables",
            )
            if key in values
        ),
    }


def _assess_requirement_readiness(
    requirements: dict[str, Any],
    command_cache: dict[str, bool] | None = None,
    dependency_cache: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Assess declarations using process metadata only; never run or install package code."""
    current_platform = (
        "windows"
        if sys.platform == "win32"
        else "macos"
        if sys.platform == "darwin"
        else "linux"
        if sys.platform.startswith("linux")
        else "other"
    )
    declared_platforms = cast(list[str], requirements.get("platforms", []))
    known_platforms = {"windows", "linux", "macos"}
    recognized = set(declared_platforms) & known_platforms
    if not declared_platforms:
        platform_status = "not_declared"
    elif current_platform in recognized:
        platform_status = "compatible"
    elif recognized:
        platform_status = "incompatible"
    else:
        platform_status = "unrecognized"

    commands = cast(list[str], requirements.get("required_command_names", []))
    checked_commands = command_cache if command_cache is not None else {}
    unchecked_commands: list[str] = []
    for name in commands:
        if name in checked_commands:
            continue
        if len(checked_commands) >= MAX_READINESS_COMMAND_CHECKS:
            unchecked_commands.append(name)
            continue
        checked_commands[name] = shutil.which(name) is not None
    available_commands = sorted(name for name in commands if checked_commands.get(name) is True)
    available_command_set = set(available_commands)
    missing_commands = sorted(name for name in commands if checked_commands.get(name) is False)
    environment_names = cast(list[str], requirements.get("required_environment_names", []))
    present_environment = sorted(name for name in environment_names if os.environ.get(name))
    present_environment_set = set(present_environment)
    dependencies_declared = requirements.get("dependencies_declared") is True
    specs = cast(list[str], requirements.get("python_dependency_specs", []))
    checked_versions = dependency_cache if dependency_cache is not None else {}
    available_specs: list[str] = []
    missing_specs: list[str] = []
    incompatible_specs: list[str] = []
    unchecked_specs: list[str] = []
    for spec in specs:
        try:
            requirement = Requirement(spec)
        except InvalidRequirement:
            unchecked_specs.append(spec)
            continue
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        distribution_name = canonicalize_name(requirement.name)
        if distribution_name not in checked_versions:
            if len(checked_versions) >= MAX_READINESS_DEPENDENCY_CHECKS:
                unchecked_specs.append(spec)
                continue
            try:
                checked_versions[distribution_name] = importlib_metadata.version(requirement.name)
            except importlib_metadata.PackageNotFoundError:
                checked_versions[distribution_name] = None
        installed_version = checked_versions[distribution_name]
        if installed_version is None:
            missing_specs.append(spec)
            continue
        try:
            parsed_version = Version(installed_version)
        except InvalidVersion:
            unchecked_specs.append(spec)
            continue
        if requirement.specifier and parsed_version not in requirement.specifier:
            incompatible_specs.append(spec)
        else:
            available_specs.append(spec)
    dependency_count = int(requirements.get("dependency_count", 0))
    unverified_dependency_count = int(requirements.get("unsupported_dependency_count", 0))
    unverified_dependency_count += int(requirements.get("unparsed_dependency_count", 0))
    if requirements.get("dependency_names_truncated") is True:
        unverified_dependency_count += 1
    if (
        dependencies_declared
        and dependency_count > 0
        and not specs
        and not unverified_dependency_count
    ):
        # Older callers may supply only aggregate counts; do not infer readiness.
        unverified_dependency_count = dependency_count
    unresolved_dependency_count = (
        len(missing_specs)
        + len(incompatible_specs)
        + len(unchecked_specs)
        + unverified_dependency_count
    )
    dependencies_unresolved = dependencies_declared and unresolved_dependency_count > 0
    raw_unverified_packages = requirements.get("unverified_dependency_packages", [])
    safe_unverified_packages: list[dict[str, str]] = []
    if isinstance(raw_unverified_packages, list):
        for raw_item in cast(list[Any], raw_unverified_packages)[:128]:
            if not isinstance(raw_item, dict):
                continue
            package_entry = cast(dict[str, Any], raw_item)
            manager = package_entry.get("manager")
            name = package_entry.get("name")
            if not isinstance(manager, str) or not isinstance(name, str):
                continue
            if manager != "unspecified" and not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_.-]{0,31}", manager
            ):
                continue
            valid_name = (
                _NPM_PACKAGE_NAME.fullmatch(name) is not None
                if manager == "npm"
                else re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) is not None
            )
            if not valid_name:
                continue
            safe_unverified_packages.append({"manager": manager, "name": name})
    toolsets = cast(list[str], requirements.get("hermes_toolsets", []))
    hermes_tools = cast(list[str], requirements.get("hermes_required_tools", []))
    fallback_toolsets = cast(list[str], requirements.get("hermes_fallback_toolsets", []))
    fallback_tools = cast(list[str], requirements.get("hermes_fallback_tools", []))
    return {
        "current_platform": current_platform,
        "platform_status": platform_status,
        "unknown_platform_names": sorted(set(declared_platforms) - known_platforms),
        "available_command_names": available_commands,
        "missing_command_names": sorted(set(missing_commands) - available_command_set),
        "unchecked_command_names": sorted(set(unchecked_commands)),
        "present_environment_names": present_environment,
        "missing_environment_names": sorted(set(environment_names) - present_environment_set),
        "dependencies_unresolved": dependencies_unresolved,
        "dependency_count_unresolved": unresolved_dependency_count,
        "available_python_dependency_specs": available_specs,
        "missing_python_dependency_specs": missing_specs,
        "incompatible_python_dependency_specs": incompatible_specs,
        "unchecked_python_dependency_specs": unchecked_specs,
        "unverified_dependency_packages": safe_unverified_packages,
        "unverified_dependency_count": unverified_dependency_count,
        "dependency_count": dependency_count,
        "hermes_toolsets_unresolved": toolsets,
        "hermes_tools_unresolved": hermes_tools,
        "hermes_fallback_toolsets_unresolved": fallback_toolsets,
        "hermes_fallback_tools_unresolved": fallback_tools,
        "not_assessed": [
            field
            for field, present in (
                ("dependency_installation", dependencies_unresolved),
                ("unsupported_dependency_managers", unverified_dependency_count > 0),
                ("dependency_check_budget", len(unchecked_specs) > 0),
                ("hermes_toolsets", bool(toolsets)),
                ("hermes_tools", bool(hermes_tools)),
                ("hermes_fallback_conditions", bool(fallback_toolsets or fallback_tools)),
                ("command_semantics", bool(commands)),
                ("command_presence_budget", bool(unchecked_commands)),
            )
            if present
        ],
    }


def _parse_skill_frontmatter(
    raw: bytes, directory_name: str
) -> tuple[str, list[str], str | None, dict[str, Any], str | None]:
    """Parse and validate the bounded Agent Skills frontmatter prefix."""
    prefix = raw[:16_384].decode("utf-8-sig")
    if (
        not prefix.startswith("---")
        or not prefix.startswith("\n", 3)
        and not prefix.startswith("\r\n", 3)
    ):
        raise ValueError("SKILL.md must begin with YAML frontmatter")
    closing = re.search(r"\n---[ \t]*\r?\n", prefix[3:])
    if closing is None:
        raise ValueError("SKILL.md frontmatter is missing its closing delimiter within 16 KiB")
    try:
        frontmatter = prefix[3 : closing.start() + 3]
        yaml_module = cast(Any, yaml)
        tokens = cast(Iterable[object], yaml_module.scan(frontmatter))
        if any(isinstance(token, yaml.tokens.AliasToken) for token in tokens):
            raise ValueError("YAML aliases are not supported in SKILL.md frontmatter")
        metadata = yaml.safe_load(frontmatter)
    except ValueError:
        raise
    except yaml.YAMLError as exc:
        raise ValueError("SKILL.md frontmatter is not valid YAML") from exc
    if not isinstance(metadata, dict):
        raise TypeError("SKILL.md frontmatter must be an object")
    values = cast(dict[str, Any], metadata)
    name = values.get("name")
    description = values.get("description")
    if (
        not isinstance(name, str)
        or name != directory_name
        or len(name) > 64
        or not _SKILL_NAME.fullmatch(name)
    ):
        raise ValueError("frontmatter name must match its directory and Agent Skills constraints")
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        raise ValueError("frontmatter description must be 1 to 1024 characters")
    version = values.get("version")
    if isinstance(version, (int, float)) and not isinstance(version, bool):
        version = str(version)
    if version is not None and (not isinstance(version, str) or len(version) > 128):
        raise ValueError("frontmatter version must be a string of at most 128 characters")
    if "license" in values and not isinstance(values["license"], str):
        raise ValueError("frontmatter license must be a string")
    if "compatibility" in values and (
        not isinstance(values["compatibility"], str) or not 1 <= len(values["compatibility"]) <= 500
    ):
        raise ValueError("frontmatter compatibility must be 1 to 500 characters")
    if "metadata" in values:
        if not isinstance(values["metadata"], dict):
            raise ValueError("frontmatter metadata must map strings to strings")
        skill_metadata = cast(dict[str, Any], values["metadata"])
        hermes_value = skill_metadata.get("hermes")
        if any(
            (key == "hermes" and not isinstance(value, dict))
            or (key != "hermes" and not isinstance(value, str))
            for key, value in skill_metadata.items()
        ):
            raise ValueError("frontmatter metadata must map strings to strings")
        hermes_metadata = (
            cast(dict[str, Any], hermes_value) if isinstance(hermes_value, dict) else None
        )
    else:
        skill_metadata = {}
        hermes_metadata = None
    category = skill_metadata.get("category")
    if isinstance(hermes_metadata, dict):
        hermes_category = hermes_metadata.get("category")
        if hermes_category is not None and not isinstance(hermes_category, str):
            raise ValueError("frontmatter metadata.hermes.category must be a string")
        if category is not None and hermes_category is not None and category != hermes_category:
            raise ValueError("frontmatter category fields disagree")
        if category is None:
            category = hermes_category
    if category is not None and not _CATEGORY.fullmatch(category):
        raise ValueError("frontmatter metadata.category must be a lowercase slug")
    raw_tools = values.get("allowed-tools", "")
    if not isinstance(raw_tools, str):
        raise TypeError("frontmatter allowed-tools must be a string")
    return description, raw_tools.split(), category, _declared_requirements(values), version


def _update_skill_frontmatter(
    content: str, *, directory_name: str, description: str, category: str
) -> str:
    _parse_skill_frontmatter(content.encode("utf-8"), directory_name)
    prefix = content[:16_384]
    closing = re.search(r"\n---[ \t]*\r?\n", prefix[3:])
    if closing is None:
        raise ValueError("SKILL.md frontmatter is missing its closing delimiter within 16 KiB")
    values = yaml.safe_load(prefix[3 : closing.start() + 3])
    if not isinstance(values, dict):
        raise TypeError("SKILL.md frontmatter must be an object")
    values = cast(dict[str, Any], values)
    values["description"] = description
    metadata = values.get("metadata", {})
    if not isinstance(metadata, dict):
        raise TypeError("SKILL.md metadata must be an object")
    metadata = cast(dict[str, Any], metadata)
    if category:
        if not _CATEGORY.fullmatch(category):
            raise ValueError("Procedure category must be a lowercase slug.")
        metadata["category"] = category
    else:
        metadata.pop("category", None)
    if metadata:
        values["metadata"] = metadata
    else:
        values.pop("metadata", None)
    serialized = yaml.safe_dump(values, sort_keys=False, allow_unicode=True)
    updated = f"---\n{serialized}---" + content[3 + closing.end() :]
    if len(updated.encode("utf-8")) > MAX_PROCEDURE_TEXT_BYTES:
        raise ValueError("Updated SKILL.md exceeds the 80 KiB import limit.")
    return updated


def _json(path: Path, package_root: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if not _inside(resolved, package_root) or not resolved.is_file():
        raise ValueError("manifest path escapes package or is not a file")
    if resolved.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds size limit")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TypeError("manifest root must be an object")
    return cast(dict[str, Any], value)


def _skill_summary(
    package_root: Path,
    directory: Path,
    skill_file: Path,
    command_cache: dict[str, bool],
    dependency_cache: dict[str, str | None],
) -> tuple[PluginSkill, str | None]:
    resolved = skill_file.resolve(strict=True)
    if not _inside(resolved, package_root) or not resolved.is_file():
        raise ValueError("skill file escapes package")
    if resolved.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("skill file exceeds size limit")
    with resolved.open("rb") as source:
        prefix = source.read(16_384)
    description, allowed_tools, category, declared_requirements, version = _parse_skill_frontmatter(
        prefix, directory.name
    )
    return (
        PluginSkill(
            name=directory.name,
            path=skill_file.relative_to(package_root).as_posix(),
            description=description,
            category=category,
            allowed_tools=allowed_tools,
            declared_requirements=declared_requirements,
            requirement_readiness=_assess_requirement_readiness(
                declared_requirements, command_cache, dependency_cache
            ),
        ),
        version,
    )


def _skill_summaries(
    package_root: Path,
    command_cache: dict[str, bool],
    dependency_cache: dict[str, str | None],
) -> tuple[list[PluginSkill], list[str]]:
    skills_root = package_root / "skills"
    if not skills_root.exists() and not skills_root.is_symlink():
        return [], []
    diagnostics: list[str] = []
    try:
        resolved_skills_root = skills_root.resolve(strict=True)
        if not _inside(resolved_skills_root, package_root.resolve(strict=True)):
            return [], ["skills component escapes package"]
        if not resolved_skills_root.is_dir():
            return [], ["skills component is not a directory"]
    except (OSError, RuntimeError, ValueError) as exc:
        return [], [f"skills component could not be read: {str(exc)[:220]}"]
    summaries: list[PluginSkill] = []
    try:
        directories = sorted(
            islice(resolved_skills_root.iterdir(), MAX_SKILLS), key=lambda item: item.name
        )
    except OSError as exc:
        return [], [f"skills component could not be enumerated: {str(exc)[:220]}"]
    for directory in directories:
        skill_file = directory / "SKILL.md"
        if not skill_file.exists() and not skill_file.is_symlink():
            continue
        try:
            summary, _ = _skill_summary(
                package_root.resolve(strict=True),
                directory,
                skill_file,
                command_cache,
                dependency_cache,
            )
            summaries.append(summary)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            diagnostics.append(f"skill {directory.name!r}: {str(exc)[:220]}")
    return summaries, diagnostics


def _hermes_skill_package(
    path: Path,
    root: Path,
    command_cache: dict[str, bool],
    dependency_cache: dict[str, str | None],
) -> PluginSummary:
    package_root = path.resolve(strict=True)
    if not _inside(package_root, root) or not package_root.is_dir():
        raise ValueError("skill directory escapes selected root")
    skill_file = package_root / "SKILL.md"
    skill, version = _skill_summary(
        package_root, package_root, skill_file, command_cache, dependency_cache
    )
    return PluginSummary(
        name=package_root.name,
        version=version,
        description=skill.description,
        path=str(package_root),
        skills=[skill],
        servers=[],
    )


def _servers(package_root: Path) -> tuple[list[PluginServer], list[str]]:
    config_path = package_root / "mcp.json"
    if not config_path.exists() and not config_path.is_symlink():
        return [], []
    diagnostics: list[str] = []
    try:
        data = _json(config_path, package_root)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return [], [f"mcp.json could not be read: {str(exc)[:240]}"]
    if set(data) != {"$schema", "mcpServers"} or data.get("$schema") != MCP_SCHEMA_V1:
        return [], ["mcp.json declares an unsupported schema or top-level shape"]
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return [], ["mcp.json mcpServers must be an object"]
    servers = cast(dict[str, Any], servers)
    if len(servers) > MAX_SERVERS:
        diagnostics.append(f"mcp.json has more than {MAX_SERVERS} servers; excess entries ignored")
    result: list[PluginServer] = []
    for name, raw_spec in list(servers.items())[:MAX_SERVERS]:
        try:
            if not name or len(name) > 128:
                raise ValueError("server name is invalid")
            if not isinstance(raw_spec, dict):
                raise TypeError("server entry must be an object")
            spec = cast(dict[str, Any], raw_spec)
            transport = spec.get("type")
            if transport == "stdio":
                if set(spec) - {"type", "command", "args", "env", "cwd"}:
                    raise ValueError("stdio entry has unsupported fields")
                command = spec.get("command")
                args = spec.get("args", [])
                env = spec.get("env", {})
                if (
                    not isinstance(command, str)
                    or not command.strip()
                    or command != command.strip()
                    or any(character.isspace() for character in command)
                ):
                    raise ValueError("stdio command must be one executable token")
                if command.startswith("./"):
                    if "\\" in command or not _inside(
                        (package_root / command[2:]).resolve(strict=False),
                        package_root.resolve(strict=True),
                    ):
                        raise ValueError("stdio command escapes the package root")
                elif "/" in command or "\\" in command or command in {".", ".."}:
                    raise ValueError(
                        "stdio command must be a bare executable or package-relative path"
                    )
                if not isinstance(args, list):
                    raise ValueError("stdio args must be an array of strings")
                args = cast(list[Any], args)
                if len(args) > 64 or any(
                    not isinstance(arg, str) or len(arg) > 2048 for arg in args
                ):
                    raise ValueError("stdio args must be at most 64 strings of 2 KiB each")
                if not isinstance(env, dict):
                    raise ValueError("stdio env must be an object of strings")
                env = cast(dict[str, Any], env)
                if any(
                    not key
                    or "=" in key
                    or "\x00" in key
                    or key.upper() in {"PLUGIN_ROOT", "PLUGIN_DATA"}
                    or not isinstance(value, str)
                    or len(value) > 8192
                    or re.search(
                        r"\$\{(?!PLUGIN_ROOT\}|PLUGIN_DATA\}|[A-Za-z_][A-Za-z0-9_]{0,127}\})", value
                    )
                    for key, value in env.items()
                ):
                    raise ValueError("stdio env keys/values are invalid or use reserved names")
                if any(
                    re.search(
                        r"\$\{(?!PLUGIN_ROOT\}|PLUGIN_DATA\}|[A-Za-z_][A-Za-z0-9_]{0,127}\})",
                        arg,
                    )
                    for arg in args
                ):
                    raise ValueError("stdio args contain an unsupported placeholder")
                cwd = spec.get("cwd")
                if "cwd" in spec:
                    if not isinstance(cwd, str):
                        raise ValueError("stdio cwd must be a supported relative path")
                    if cwd in {"${PLUGIN_ROOT}", "${PLUGIN_DATA}"}:
                        pass
                    elif cwd.startswith("${PLUGIN_ROOT}/"):
                        cwd_relative = cwd[len("${PLUGIN_ROOT}/") :]
                        if not cwd_relative or "\\" in cwd_relative:
                            raise ValueError("stdio cwd has an invalid PLUGIN_ROOT path")
                        if not _inside(
                            (package_root / cwd_relative).resolve(strict=False),
                            package_root.resolve(strict=True),
                        ):
                            raise ValueError("stdio cwd escapes the package root")
                    elif cwd.startswith("${PLUGIN_DATA}/"):
                        relative = PurePosixPath(cwd[len("${PLUGIN_DATA}/") :])
                        if (
                            not relative.parts
                            or relative.is_absolute()
                            or ".." in relative.parts
                            or "\\" in cwd
                        ):
                            raise ValueError("stdio cwd has an invalid PLUGIN_DATA path")
                    elif cwd.startswith("./"):
                        if "\\" in cwd or not _inside(
                            (package_root / cwd[2:]).resolve(strict=False),
                            package_root.resolve(strict=True),
                        ):
                            raise ValueError("stdio cwd escapes the package root")
                    else:
                        raise ValueError(
                            "stdio cwd must be plugin-relative or use a supported placeholder"
                        )
                result.append(
                    PluginServer(
                        name=name,
                        transport="stdio",
                        command_name=Path(command.replace("\\", "/")).name,
                        argument_count=len(args),
                        environment_names=sorted(env),
                    )
                )
            elif transport == "streamable-http":
                if set(spec) - {"type", "url", "headers", "env"}:
                    raise ValueError("HTTP entry has unsupported fields")
                endpoint = spec.get("url")
                if not isinstance(endpoint, str) or len(endpoint) > 2048:
                    raise ValueError("HTTP endpoint is invalid")
                parsed = urlsplit(endpoint)
                if (
                    parsed.scheme not in {"https", "http"}
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.fragment
                ):
                    raise ValueError("HTTP endpoint is not an allowed URL")
                headers_value = spec.get("headers", {})
                if not isinstance(headers_value, dict):
                    raise ValueError("HTTP headers must be an object of strings")
                headers = cast(dict[str, Any], headers_value)
                environment = spec.get("env", {})
                if not isinstance(environment, dict):
                    raise ValueError("HTTP env must be an object of strings")
                if any(
                    not isinstance(value, str)
                    or len(value) > 8192
                    or re.search(r"\$\{(?![A-Za-z_][A-Za-z0-9_]{0,127}\})", value)
                    for value in cast(dict[Any, Any], environment).values()
                ):
                    raise ValueError("HTTP environment values are invalid")
                lowered_headers = [key.lower() for key in headers]
                if len(lowered_headers) != len(set(lowered_headers)) or any(
                    not _HEADER.fullmatch(key)
                    or not isinstance(value, str)
                    or "\r" in value
                    or "\n" in value
                    or any(
                        name not in environment
                        for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]{0,127})\}", value)
                    )
                    or re.search(r"\$\{(?![A-Za-z_][A-Za-z0-9_]{0,127}\})", value)
                    for key, value in headers.items()
                ):
                    raise ValueError("HTTP headers are invalid or contain duplicate names")
                if parsed.scheme == "http":
                    try:
                        loopback = (
                            parsed.hostname == "localhost"
                            or ipaddress.ip_address(parsed.hostname).is_loopback
                        )
                    except ValueError:
                        loopback = False
                    if not loopback:
                        raise ValueError("cleartext HTTP endpoint must use loopback")
                if parsed.port == 0:
                    raise ValueError("HTTP endpoint cannot use port zero")
                port = f":{parsed.port}" if parsed.port is not None else ""
                safe_endpoint = f"{parsed.scheme}://{parsed.hostname}{port}/…"
                result.append(
                    PluginServer(
                        name=name,
                        transport="streamable_http",
                        environment_names=sorted(
                            key for key in cast(dict[Any, Any], environment) if isinstance(key, str)
                        ),
                        endpoint=safe_endpoint,
                    )
                )
            else:
                raise ValueError(f"unsupported MCP transport {transport!r}")
        except (TypeError, ValueError) as exc:
            diagnostics.append(f"MCP server {name!r}: {str(exc)[:220]}")
    return result, diagnostics


def read_plugin_mcp_server(directory: str, package_name: str, server_name: str) -> dict[str, Any]:
    """Read one validated MCP declaration from a freshly discovered plugin package."""
    discovery = discover_agent_plugins(directory)
    package = next((item for item in discovery.packages if item.name == package_name), None)
    if package is None:
        raise ValueError("selected package is not a valid discovered package")
    server = next((item for item in package.servers if item.name == server_name), None)
    if server is None:
        raise ValueError("selected MCP server is not present in the discovered package")
    package_root = Path(package.path).resolve(strict=True)
    data = _json(package_root / "mcp.json", package_root)
    raw_servers: Any = data.get("mcpServers")
    if not isinstance(raw_servers, dict) or server_name not in raw_servers:
        raise ValueError("selected MCP server changed since discovery")
    raw_spec_value: Any = cast(Any, raw_servers)[server_name]
    if not isinstance(raw_spec_value, dict):
        raise TypeError("selected MCP server changed since discovery")
    selected = cast(dict[str, Any], raw_spec_value)
    if server.transport == "stdio":
        spec: dict[str, Any] = {
            "type": "stdio",
            "command": selected.get("command"),
            "args": selected.get("args", []),
            "env": selected.get("env", {}),
            "cwd": selected.get("cwd"),
        }
        if not isinstance(spec["command"], str) or not isinstance(spec["args"], list):
            raise ValueError("selected MCP server changed since discovery")
        if not isinstance(spec["env"], dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or len(value) > 8192
            or re.search(
                r"\$\{(?!PLUGIN_ROOT\}|PLUGIN_DATA\}|[A-Za-z_][A-Za-z0-9_]{0,127}\})",
                value,
            )
            for key, value in cast(dict[Any, Any], spec["env"]).items()
        ):
            raise ValueError("selected MCP environment changed since discovery")
        if any(
            not isinstance(argument, str)
            or len(argument) > 2048
            or re.search(
                r"\$\{(?!PLUGIN_ROOT\}|PLUGIN_DATA\}|[A-Za-z_][A-Za-z0-9_]{0,127}\})",
                argument,
            )
            for argument in cast(list[Any], spec["args"])
        ):
            raise ValueError("selected MCP arguments changed since discovery")
        if spec["cwd"] is not None and not isinstance(spec["cwd"], str):
            raise ValueError("selected MCP working directory changed since discovery")
        _validated, diagnostics = _servers(package_root)
        if any(item.startswith(f"MCP server {server_name!r}:") for item in diagnostics):
            raise ValueError("selected MCP server no longer passes package validation")
        return {
            "package_root": str(package_root),
            "package_name": package.name,
            "server_name": server.name,
            "spec": spec,
        }
    endpoint = selected.get("url")
    headers = selected.get("headers", {})
    if not isinstance(endpoint, str) or not isinstance(headers, dict):
        raise TypeError("selected MCP server changed since discovery")
    header_values: dict[str, str] = {}
    for header_name, header_value in cast(dict[Any, Any], headers).items():
        if not isinstance(header_name, str) or not isinstance(header_value, str):
            raise TypeError("selected MCP header configuration changed since discovery")
        env_values: Any = selected.get("env", {})
        if not isinstance(env_values, dict):
            raise TypeError("selected MCP environment changed since discovery")
        substitutions = re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]{0,127})\}", header_value)
        if re.search(r"\$\{(?![A-Za-z_][A-Za-z0-9_]{0,127}\})", header_value):
            raise ValueError("MCP header uses an unsupported credential placeholder")
        if any(name not in env_values for name in substitutions):
            raise ValueError("MCP header references an undeclared environment variable")
        header_values[header_name] = header_value
    return {
        "package_root": str(package_root),
        "package_name": package.name,
        "server_name": server.name,
        "spec": {
            "type": "streamable-http",
            "url": endpoint,
            "headers": header_values,
            "env": selected.get("env", {}),
        },
    }


def _package(
    path: Path,
    root: Path,
    command_cache: dict[str, bool],
    dependency_cache: dict[str, str | None],
) -> tuple[PluginSummary, list[str]]:
    package_root = path.resolve(strict=True)
    if not _inside(package_root, root) or not package_root.is_dir():
        raise ValueError("package directory escapes selected root")
    manifest = _json(package_root / "plugin.json", package_root)
    name = manifest.get("name")
    version = manifest.get("version")
    description = manifest.get("description")
    if manifest.get("$schema") != PLUGIN_SCHEMA_V1:
        raise ValueError("plugin.json declares an unsupported or missing Agent Plugins schema")
    if not isinstance(name, str) or len(name) > 64 or not _PLUGIN_NAME.fullmatch(name):
        raise ValueError("plugin.json name does not satisfy Agent Plugins v1 constraints")
    # Agent Plugins v1 requires clients to report and ignore unknown top-level
    # fields. They do not invalidate otherwise well-formed components.
    if "version" in manifest and not isinstance(version, str):
        raise ValueError("plugin.json version must be a string")
    if "description" in manifest and not isinstance(description, str):
        raise ValueError("plugin.json description must be a string")
    author = manifest.get("author")
    if "author" in manifest:
        if not isinstance(author, dict):
            raise ValueError("plugin.json author must contain only name, email, and url strings")
        author = cast(dict[str, Any], author)
        if set(author) - {"name", "email", "url"}:
            raise ValueError("plugin.json author must contain only name, email, and url strings")
        if any(not isinstance(value, str) for value in author.values()):
            raise ValueError("plugin.json author must contain only name, email, and url strings")
    for field in ("homepage", "repository", "license"):
        if field in manifest and not isinstance(manifest[field], str):
            raise ValueError(f"plugin.json {field} must be a string")
    keywords = manifest.get("keywords")
    if "keywords" in manifest and (
        not isinstance(keywords, list)
        or any(not isinstance(keyword, str) for keyword in cast(list[Any], keywords))
    ):
        raise ValueError("plugin.json keywords must be an array of strings")
    # No extension namespace is implemented by X.A.A.; ignore unimplemented data.
    servers, server_diagnostics = _servers(package_root)
    skills, skill_diagnostics = _skill_summaries(package_root, command_cache, dependency_cache)
    summary = PluginSummary(
        name=name,
        version=version,
        description=description,
        path=str(package_root),
        skills=skills,
        servers=servers,
    )
    return summary, skill_diagnostics + server_diagnostics


def discover_agent_plugins(directory: str) -> PluginDiscovery:
    """Inspect bounded package and standalone Hermes skill directories without execution."""
    requested = Path(directory).expanduser()
    try:
        root = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("selected package directory does not exist") from exc
    if not root.is_dir():
        raise ValueError("selected package root is not a directory")
    candidates = (
        [root]
        if (root / "plugin.json").exists()
        else [root]
        if (root / "SKILL.md").exists()
        else sorted(islice(root.iterdir(), MAX_PACKAGES), key=lambda item: item.name)
    )
    packages: list[PluginSummary] = []
    diagnostics: list[PluginDiagnostic] = []
    command_cache: dict[str, bool] = {}
    dependency_cache: dict[str, str | None] = {}
    for candidate in candidates[:MAX_PACKAGES]:
        if candidate.name.startswith("."):
            continue
        try:
            resolved = candidate.resolve(strict=True)
            if not _inside(resolved, root):
                raise ValueError("package directory escapes selected root")
            if not resolved.is_dir():
                continue
            if (resolved / "plugin.json").exists():
                summary, server_diagnostics = _package(
                    resolved, root, command_cache, dependency_cache
                )
                packages.append(summary)
                diagnostics.extend(
                    PluginDiagnostic(path=candidate.name[:256], message=message)
                    for message in server_diagnostics
                )
                manifest = _json(resolved / "plugin.json", resolved)
                known_fields = {
                    "$schema",
                    "name",
                    "version",
                    "description",
                    "author",
                    "homepage",
                    "repository",
                    "license",
                    "keywords",
                    "extensions",
                }
                for field in sorted(set(manifest) - known_fields):
                    diagnostics.append(
                        PluginDiagnostic(
                            path=candidate.name[:256],
                            message=f"plugin.json has unsupported field {field!r}; ignored",
                        )
                    )
                if "extensions" in manifest and not isinstance(manifest["extensions"], dict):
                    diagnostics.append(
                        PluginDiagnostic(
                            path=candidate.name[:256],
                            message="plugin.json extensions is not an object; ignored",
                        )
                    )
            elif (resolved / "SKILL.md").exists():
                packages.append(
                    _hermes_skill_package(resolved, root, command_cache, dependency_cache)
                )
        except (OSError, RuntimeError, ValueError) as exc:
            diagnostics.append(PluginDiagnostic(path=candidate.name[:256], message=str(exc)[:300]))
    return PluginDiscovery(root=str(root), packages=packages, diagnostics=diagnostics)


def plugin_source_sha256(directory: Path) -> str:
    """Hash a bounded package tree while refusing symlinks and changing files."""
    try:
        root = directory.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("installed MCP package folder is unavailable") from exc
    if not root.is_dir():
        raise ValueError("installed MCP package folder is not a directory")
    digest = hashlib.sha256()
    entry_count = 0
    file_count = 0
    total_bytes = 0
    seen_paths: set[str] = set()

    def raise_walk_error(error: OSError) -> None:
        raise ValueError("installed MCP package could not be read") from error

    for current, directories, files in os.walk(
        root, topdown=True, followlinks=False, onerror=raise_walk_error
    ):
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        current_path = Path(current)
        for name in [*directories, *files]:
            entry_count += 1
            if entry_count > MAX_PLUGIN_FINGERPRINT_ENTRIES:
                raise ValueError("installed MCP package exceeds the 1024-entry verification limit")
            path = current_path / name
            is_junction = getattr(path, "is_junction", lambda: False)
            if path.is_symlink() or is_junction():
                raise ValueError("installed MCP package contains a link or junction")
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError("installed MCP package contains an unavailable path") from exc
            if not _inside(resolved, root):
                raise ValueError("installed MCP package path escapes its root")
            relative_name = path.relative_to(root).as_posix()
            key = relative_name.casefold()
            if key in seen_paths:
                raise ValueError("installed MCP package contains duplicate case-insensitive paths")
            seen_paths.add(key)
            relative = relative_name.encode("utf-8")
            if name in directories:
                digest.update(b"D" + len(relative).to_bytes(4, "big") + relative)
                continue
            try:
                before = path.stat()
            except OSError as exc:
                raise ValueError("installed MCP package file could not be read") from exc
            if not path.is_file() or before.st_size > MAX_PLUGIN_FINGERPRINT_FILE_BYTES:
                raise ValueError("installed MCP package contains an unsupported or oversized file")
            file_count += 1
            total_bytes += before.st_size
            if (
                file_count > MAX_PLUGIN_FINGERPRINT_ENTRIES
                or total_bytes > MAX_PLUGIN_FINGERPRINT_TOTAL_BYTES
            ):
                raise ValueError("installed MCP package exceeds its source verification limits")
            digest.update(b"F" + len(relative).to_bytes(4, "big") + relative)
            digest.update(before.st_size.to_bytes(8, "big"))
            read_bytes = 0
            try:
                with path.open("rb") as source:
                    while chunk := source.read(64 * 1024):
                        read_bytes += len(chunk)
                        if read_bytes > MAX_PLUGIN_FINGERPRINT_FILE_BYTES:
                            raise ValueError(
                                "installed MCP package file exceeds its verification limit"
                            )
                        digest.update(chunk)
                after = path.stat()
            except OSError as exc:
                raise ValueError("installed MCP package file could not be read") from exc
            if read_bytes != before.st_size or (
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("installed MCP package changed during source verification")
    if not file_count:
        raise ValueError("installed MCP package is empty")
    return digest.hexdigest()


def install_agent_plugin_archive(archive_path: str, managed_root: Path) -> PluginInstallResult:
    """Safely extract and validate a package ZIP without executing its contents."""
    try:
        source = Path(archive_path).expanduser().resolve(strict=True)
        if not source.is_file() or source.stat().st_size > MAX_INSTALL_ARCHIVE_BYTES:
            raise ValueError("selected plugin archive is missing or exceeds the 16 MiB limit")
        root = managed_root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(
            "selected plugin archive or managed package directory is unavailable"
        ) from exc

    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_INSTALL_FILES:
                raise ValueError("plugin archive must contain between 1 and 1024 entries")
            seen: set[str] = set()
            total_declared = 0
            for entry in entries:
                name = entry.filename
                relative = PurePosixPath(name.rstrip("/"))
                mode = entry.external_attr >> 16
                kind = mode & 0o170000
                if (
                    not name
                    or "\x00" in name
                    or "\\" in name
                    or relative.is_absolute()
                    or any(part in {"", ".", ".."} for part in relative.parts)
                    or any(":" in part for part in relative.parts)
                    or name.startswith("/")
                    or entry.flag_bits & 0x1
                    or kind not in {0, 0o100000, 0o040000}
                ):
                    raise ValueError("plugin archive contains an unsafe or unsupported path")
                key = relative.as_posix().casefold()
                if key in seen:
                    raise ValueError("plugin archive contains duplicate paths")
                seen.add(key)
                if entry.is_dir():
                    continue
                if entry.file_size > MAX_INSTALL_FILE_BYTES:
                    raise ValueError("plugin archive contains a file over the 2 MiB limit")
                total_declared += entry.file_size
                if total_declared > MAX_INSTALL_TOTAL_BYTES:
                    raise ValueError("plugin archive expands beyond the 16 MiB limit")

            if "plugin.json" not in seen:
                raise ValueError("plugin archive must contain plugin.json at its root")

            with tempfile.TemporaryDirectory(prefix="install-", dir=root) as temporary:
                stage = Path(temporary)
                total_written = 0
                for entry in entries:
                    relative = PurePosixPath(entry.filename.rstrip("/"))
                    destination = stage.joinpath(*relative.parts)
                    if entry.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with archive.open(entry) as source_file, destination.open("xb") as target:
                        while chunk := source_file.read(64 * 1024):
                            written += len(chunk)
                            total_written += len(chunk)
                            if (
                                written > MAX_INSTALL_FILE_BYTES
                                or total_written > MAX_INSTALL_TOTAL_BYTES
                            ):
                                raise ValueError("plugin archive expanded beyond its size limit")
                            target.write(chunk)
                    if written != entry.file_size:
                        raise ValueError(
                            "plugin archive entry size does not match its directory record"
                        )

                discovery = discover_agent_plugins(str(stage))
                package = next(
                    (
                        item
                        for item in discovery.packages
                        if Path(item.path).resolve(strict=True) == stage.resolve(strict=True)
                    ),
                    None,
                )
                if package is None or not _PLUGIN_NAME.fullmatch(package.name):
                    detail = "; ".join(item.message for item in discovery.diagnostics[:3])
                    suffix = f": {detail}" if detail else ""
                    raise ValueError(
                        f"plugin archive does not contain a valid root package{suffix}"
                    )
                target = root / package.name
                if target.exists():
                    raise FileExistsError(f"plugin package {package.name!r} is already installed")
                os.replace(stage, target)
                installed = discover_agent_plugins(str(target)).packages
                installed_package = next(
                    (item for item in installed if item.name == package.name), None
                )
                if installed_package is None:
                    shutil.rmtree(target, ignore_errors=True)
                    raise ValueError("installed plugin package failed its final validation")
                return PluginInstallResult(root=str(root), package=installed_package)
    except FileExistsError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError("selected plugin archive is invalid or could not be installed") from exc


def remove_installed_agent_plugin(managed_root: Path, package_name: str) -> PluginRemovalResult:
    """Remove one explicitly selected package from X.A.A.'s managed install root."""
    if not _PLUGIN_NAME.fullmatch(package_name):
        raise ValueError("selected plugin package name is invalid")
    try:
        root = managed_root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("managed plugin directory is unavailable")
        candidate = root / package_name
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("managed plugin package cannot be a symbolic link or junction")
        target = candidate.resolve(strict=True)
        if target.parent != root or not target.is_dir():
            raise ValueError("selected package is not a managed plugin directory")
        shutil.rmtree(target)
    except FileNotFoundError as exc:
        raise ValueError("selected managed plugin package was not found") from exc
    except (OSError, RuntimeError) as exc:
        raise ValueError("selected managed plugin package could not be removed") from exc
    return PluginRemovalResult(removed=True, package_name=package_name)


def list_managed_plugin_data(data_root: Path) -> PluginDataListing:
    """List direct package data directories without following links."""
    root = data_root.expanduser().resolve(strict=False)
    package_names: list[str] = []
    try:
        if root.exists():
            if data_root.is_symlink() or not root.is_dir():
                raise ValueError("managed plugin data directory is unavailable")
            for child in root.iterdir():
                if (
                    _PLUGIN_NAME.fullmatch(child.name)
                    and not child.is_symlink()
                    and not getattr(child, "is_junction", lambda: False)()
                    and child.is_dir()
                ):
                    package_names.append(child.name)
    except OSError as exc:
        raise ValueError("managed plugin data directory could not be listed") from exc
    return PluginDataListing(root=str(root), package_names=sorted(package_names))


def remove_managed_plugin_data(data_root: Path, package_name: str) -> PluginDataRemovalResult:
    """Remove one explicitly selected package data directory under its managed root."""
    if not _PLUGIN_NAME.fullmatch(package_name):
        raise ValueError("selected plugin package name is invalid")
    try:
        root = data_root.expanduser().resolve(strict=True)
        if not root.is_dir() or data_root.is_symlink():
            raise ValueError("managed plugin data directory is unavailable")
        candidate = root / package_name
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("managed plugin data cannot be a symbolic link or junction")
        target = candidate.resolve(strict=True)
        if target.parent != root or not target.is_dir():
            raise ValueError("selected package data is not a managed directory")
        shutil.rmtree(target)
    except FileNotFoundError as exc:
        raise ValueError("selected managed plugin data was not found") from exc
    except (OSError, RuntimeError) as exc:
        raise ValueError("selected managed plugin data could not be removed") from exc
    return PluginDataRemovalResult(removed=True, package_name=package_name)


def read_plugin_skill(directory: str, package_name: str, skill_name: str) -> PluginSkillImport:
    """Read one bounded in-root skill selected from a fresh static discovery result."""
    discovery = discover_agent_plugins(directory)
    package = next((item for item in discovery.packages if item.name == package_name), None)
    if package is None:
        raise ValueError("selected package is not a valid discovered package")
    skill = next((item for item in package.skills if item.name == skill_name), None)
    if skill is None:
        raise ValueError("selected skill is not present in the discovered package")
    package_root = Path(package.path)
    skill_path = (package_root / skill.path).resolve(strict=True)
    if not _inside(skill_path, package_root) or not skill_path.is_file():
        raise ValueError("skill file is not a regular file within the package")
    if skill_path.stat().st_size > MAX_PROCEDURE_TEXT_BYTES:
        raise ValueError("skill file exceeds the 80 KiB procedure candidate limit")
    with skill_path.open("rb") as source:
        raw = source.read(MAX_PROCEDURE_TEXT_BYTES + 1)
    if len(raw) > MAX_PROCEDURE_TEXT_BYTES:
        raise ValueError("skill file exceeds the 80 KiB procedure candidate limit")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("skill file must be UTF-8 text") from exc
    if "\x00" in content:
        raise ValueError("skill file cannot contain NUL characters")
    description, allowed_tools, category, declared_requirements, _ = _parse_skill_frontmatter(
        raw, skill.name
    )
    asset_rows: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    links = _markdown_links(content)
    for link in links:
        parsed_link = urlsplit(link)
        if (
            parsed_link.scheme
            or parsed_link.netloc
            or not parsed_link.path
            or parsed_link.path.startswith("#")
        ):
            continue
        decoded_path = unquote(parsed_link.path)
        if (
            _PLACEHOLDER_LINK.search(decoded_path)
            or decoded_path.strip().casefold() in _PLACEHOLDER_DESTINATIONS
        ):
            continue
        relative = PurePosixPath(decoded_path)
        if (
            relative.is_absolute()
            or "\\" in decoded_path
            or decoded_path.startswith("/")
            or any(part == ".." for part in decoded_path.split("/"))
        ):
            raise ValueError("skill resource link must be a normalized relative path")
        normalized_parts = [part for part in decoded_path.split("/") if part not in {"", "."}]
        if not normalized_parts:
            raise ValueError("skill resource link must name a file")
        resource_name = PurePosixPath(*normalized_parts).as_posix()
        if resource_name in seen_paths:
            continue
        seen_paths.add(resource_name)
        if len(seen_paths) > MAX_SKILL_RESOURCE_COUNT:
            raise ValueError("skill references more than 32 supporting files")
        resource_path = (skill_path.parent / Path(*relative.parts)).resolve(strict=True)
        if not _inside(resource_path, skill_path.parent) or not resource_path.is_file():
            raise ValueError("linked skill resource escapes its skill directory or is not a file")
        with resource_path.open("rb") as source:
            resource_bytes = source.read(MAX_SKILL_RESOURCE_BYTES + 1)
        if len(resource_bytes) > MAX_SKILL_RESOURCE_BYTES:
            raise ValueError("linked skill resource exceeds the 64 KiB per-file limit")
        total_bytes += len(resource_bytes)
        if total_bytes > MAX_SKILL_RESOURCE_TOTAL_BYTES:
            raise ValueError("linked skill resources exceed the 1 MiB total limit")
        suffix = PurePosixPath(resource_name).suffix.casefold()
        media_type = (
            _SCRIPT_MEDIA_TYPES.get(suffix)
            or mimetypes.guess_type(resource_name)[0]
            or "text/plain"
        )
        if is_text_asset_media_type(media_type):
            try:
                resource_bytes.decode("utf-8-sig")
            except UnicodeError as exc:
                raise ValueError("linked text skill resources must be UTF-8") from exc
        else:
            signatures = {
                "image/gif": resource_bytes.startswith((b"GIF87a", b"GIF89a")),
                "image/jpeg": resource_bytes.startswith(b"\xff\xd8\xff"),
                "image/png": resource_bytes.startswith(b"\x89PNG\r\n\x1a\n"),
                "image/webp": (
                    len(resource_bytes) >= 12
                    and resource_bytes.startswith(b"RIFF")
                    and resource_bytes[8:12] == b"WEBP"
                ),
                "application/pdf": b"%PDF-" in resource_bytes[:1024],
            }
            if media_type not in SUPPORTED_PROCEDURE_BINARY_MEDIA_TYPES or not signatures.get(
                media_type, False
            ):
                raise ValueError("linked skill resource type or file signature is unsupported")
        asset_rows.append(
            {
                "path": resource_name,
                "content_base64": base64.b64encode(resource_bytes).decode("ascii"),
                "media_type": media_type,
                "sha256": hashlib.sha256(resource_bytes).hexdigest(),
            }
        )
    manifest_path = package_root / "plugin.json"
    has_manifest = manifest_path.exists()
    if has_manifest:
        with manifest_path.open("rb") as manifest_source:
            manifest_bytes = manifest_source.read(MAX_MANIFEST_BYTES + 1)
    else:
        # A standalone Hermes skill has no package manifest; bind its source
        # provenance to the SKILL.md bytes already read for the import.
        manifest_bytes = raw
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError("plugin.json exceeds the package manifest size limit")
    return PluginSkillImport(
        source_format="agent_plugins_v1" if has_manifest else "hermes_skill",
        package_name=package.name,
        package_version=package.version or "",
        skill_name=skill.name,
        content=content,
        description=description,
        category=category,
        allowed_tools=allowed_tools,
        declared_requirements=declared_requirements,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        assets=asset_rows,
    )


def export_procedure_package(
    *,
    identity: str,
    version: int,
    name: str,
    content: str,
    required_tools: list[dict[str, str]],
    source: dict[str, str] | None,
    assets: list[dict[str, str]],
    description: str = "",
    category: str = "",
) -> tuple[str, bytes]:
    """Build a bounded Agent Plugins ZIP containing one procedure version."""
    source = source or {}

    def slug(value: str, fallback: str) -> str:
        result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:64].rstrip("-")
        return result or fallback

    package_name = slug(
        source.get("package_name", ""), f"xaa-{slug(name, 'procedure')}-{identity[:8]}"
    )
    skill_name = source.get("skill_name", "")
    if not _SKILL_NAME.fullmatch(skill_name):
        skill_name = slug(name, "procedure")
    package_version = source.get("package_version", "")
    if not package_version or len(package_version) > 128:
        package_version = f"1.0.{version}"
    description = description or source.get("description", "")
    if not description:
        description = f"Exported X.A.A. procedure version {version}."
    description = description[:1024]
    allowed_tools = source.get("allowed_tools", "").split()
    if not allowed_tools:
        allowed_tools = [tool["name"] for tool in required_tools]
    markdown = content
    try:
        prior_description, _, prior_category, _, _ = _parse_skill_frontmatter(
            content.encode("utf-8"), skill_name
        )
        if prior_description != description or prior_category != category:
            markdown = _update_skill_frontmatter(
                content,
                directory_name=skill_name,
                description=description,
                category=category,
            )
    except (UnicodeError, TypeError, ValueError, yaml.YAMLError):
        frontmatter: dict[str, Any] = {
            "name": skill_name,
            "description": description,
            "allowed-tools": " ".join(allowed_tools),
        }
        if category:
            frontmatter["metadata"] = {"category": category}
        header = yaml.safe_dump(
            frontmatter,
            sort_keys=False,
            allow_unicode=True,
        )
        markdown = f"---\n{header}---\n\n{content}"
    if len(markdown.encode("utf-8")) > MAX_PROCEDURE_TEXT_BYTES:
        raise ValueError("Exported SKILL.md exceeds the 80 KiB import limit.")
    manifest: dict[str, Any] = {
        "$schema": PLUGIN_SCHEMA_V1,
        "name": package_name,
        "version": package_version,
        "description": (source.get("package_description") or f"Exported X.A.A. procedure {name}.")[
            :2048
        ],
        "extensions": {},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        base = f"skills/{skill_name}/"
        archive.writestr(base + "SKILL.md", markdown.encode("utf-8"))
        for asset in assets:
            relative = PurePosixPath(asset["path"])
            if (
                relative.is_absolute()
                or relative.as_posix() != asset["path"]
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise ValueError("Procedure resource path is invalid for package export.")
            raw = base64.b64decode(asset["content_base64"], validate=True)
            if (
                len(raw) > MAX_SKILL_RESOURCE_BYTES
                or hashlib.sha256(raw).hexdigest() != asset["sha256"]
            ):
                raise ValueError("Procedure resource failed its package export integrity check.")
            archive.writestr(base + relative.as_posix(), raw)
    data = buffer.getvalue()
    if len(data) > MAX_MANIFEST_BYTES + MAX_SKILL_RESOURCE_TOTAL_BYTES + MAX_SKILL_RESOURCE_BYTES:
        raise ValueError("Exported procedure package exceeds its size limit.")
    return package_name, data

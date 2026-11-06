"""Bounded, read-only discovery for Agent Plugins v1 package directories."""

from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import sys
import zipfile
from collections.abc import Iterable
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from urllib.parse import unquote, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field

MAX_PACKAGES = 128
MAX_SKILLS = 128
MAX_SERVERS = 64
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SKILL_RESOURCE_COUNT = 32
MAX_SKILL_RESOURCE_BYTES = 64 * 1024
MAX_SKILL_RESOURCE_TOTAL_BYTES = 1024 * 1024
MAX_READINESS_COMMAND_CHECKS = 256
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


class PluginSkillImport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
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
    if isinstance(dependencies, dict):
        dependency_count = len(cast(dict[str, Any], dependencies))
    elif isinstance(dependencies, list):
        dependency_count = len(cast(list[Any], dependencies))
    elif isinstance(dependencies, tuple):
        dependency_count = len(cast(tuple[Any, ...], dependencies))
    else:
        dependency_count = int(dependencies is not None)
    raw_metadata = values.get("metadata")
    metadata = cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
    raw_hermes = metadata.get("hermes")
    hermes = cast(dict[str, Any], raw_hermes) if isinstance(raw_hermes, dict) else {}
    dependency_names: set[str] = set()
    dependency_managers: set[str] = set()
    unparsed_dependency_count = 0
    dependency_items: list[Any] = []
    dependency_items_truncated = False
    if isinstance(dependencies, (list, tuple)):
        all_dependency_items = list(cast(Iterable[Any], dependencies))
        dependency_items = all_dependency_items[:128]
        dependency_items_truncated = len(all_dependency_items) > 128
    elif isinstance(dependencies, dict):
        for manager, package_items in list(cast(dict[Any, Any], dependencies).items())[:64]:
            if isinstance(manager, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,31}", manager):
                dependency_managers.add(manager.lower())
            remaining = 128 - len(dependency_items)
            if isinstance(package_items, str):
                dependency_items.extend([package_items] if remaining else [])
            elif isinstance(package_items, (list, tuple)):
                all_package_items = list(cast(Iterable[Any], package_items))
                dependency_items.extend(all_package_items[:remaining])
                dependency_items_truncated = (
                    dependency_items_truncated or len(all_package_items) > remaining
                )
            else:
                unparsed_dependency_count += 1
            if len(dependency_items) >= 128:
                dependency_items_truncated = True
                break
    elif isinstance(dependencies, str):
        dependency_items = [dependencies]
    for item in dependency_items:
        if not isinstance(item, str) or len(item) > 256:
            unparsed_dependency_count += 1
            continue
        match = _DEPENDENCY_SPEC.fullmatch(item)
        if match is None or "://" in item or item.startswith(("git+", "hg+", "svn+")):
            unparsed_dependency_count += 1
            continue
        dependency_names.add(match.group(1))
    toolsets = _bounded_names(hermes.get("requires_toolsets"))
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
        "required_command_names": commands,
        "required_environment_names": environment_names,
        "prerequisite_fields": sorted(str(key) for key in prerequisites)[:64],
        "dependencies_declared": dependencies is not None,
        "dependency_count": min(dependency_count, 128),
        "dependency_names": sorted(dependency_names)[:128],
        "dependency_names_truncated": dependency_count > 128 or dependency_items_truncated,
        "dependency_managers": sorted(dependency_managers)[:64],
        "unparsed_dependency_count": min(unparsed_dependency_count, 128),
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
    requirements: dict[str, Any], command_cache: dict[str, bool] | None = None
) -> dict[str, Any]:
    """Compare safe declaration names with this process environment; never execute them."""
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
    dependency_count = requirements.get("dependency_count", 0)
    toolsets = cast(list[str], requirements.get("hermes_toolsets", []))
    return {
        "current_platform": current_platform,
        "platform_status": platform_status,
        "unknown_platform_names": sorted(set(declared_platforms) - known_platforms),
        "available_command_names": available_commands,
        "missing_command_names": sorted(set(missing_commands) - available_command_set),
        "unchecked_command_names": sorted(set(unchecked_commands)),
        "present_environment_names": present_environment,
        "missing_environment_names": sorted(set(environment_names) - present_environment_set),
        "dependencies_unresolved": dependencies_declared,
        "dependency_count_unresolved": dependency_count if dependencies_declared else 0,
        "hermes_toolsets_unresolved": toolsets,
        "not_assessed": [
            field
            for field, present in (
                ("dependency_installation", dependencies_declared),
                ("hermes_toolsets", bool(toolsets)),
                ("command_semantics", bool(commands)),
                ("command_presence_budget", bool(unchecked_commands)),
            )
            if present
        ],
    }


def _parse_skill_frontmatter(
    raw: bytes, directory_name: str
) -> tuple[str, list[str], str | None, dict[str, Any]]:
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
    return description, raw_tools.split(), category, _declared_requirements(values)


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
    if len(updated.encode("utf-8")) > 16_384:
        raise ValueError("Updated SKILL.md exceeds the 16 KiB import limit.")
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


def _skill_summaries(package_root: Path, command_cache: dict[str, bool]) -> list[PluginSkill]:
    skills_root = package_root / "skills"
    if not skills_root.exists():
        return []
    if not _inside(skills_root.resolve(strict=True), package_root):
        raise ValueError("skills directory escapes package")
    summaries: list[PluginSkill] = []
    for directory in sorted(islice(skills_root.iterdir(), MAX_SKILLS), key=lambda item: item.name):
        if not _SLUG.fullmatch(directory.name):
            continue
        skill_file = directory / "SKILL.md"
        if not skill_file.exists():
            continue
        resolved = skill_file.resolve(strict=True)
        if not _inside(resolved, package_root) or not resolved.is_file():
            raise ValueError("skill file escapes package")
        if resolved.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("skill file exceeds size limit")
        with resolved.open("rb") as source:
            prefix = source.read(16_384)
        description, allowed_tools, category, declared_requirements = _parse_skill_frontmatter(
            prefix, directory.name
        )
        summaries.append(
            PluginSkill(
                name=directory.name,
                path=skill_file.relative_to(package_root).as_posix(),
                description=description,
                category=category,
                allowed_tools=allowed_tools,
                declared_requirements=declared_requirements,
                requirement_readiness=_assess_requirement_readiness(
                    declared_requirements, command_cache
                ),
            )
        )
    return summaries


def _servers(manifest: dict[str, Any], package_root: Path) -> list[PluginServer]:
    config_path = package_root / "mcp.json"
    if not config_path.exists():
        return []
    data = _json(config_path, package_root)
    if set(data) != {"$schema", "mcpServers"} or data.get("$schema") != MCP_SCHEMA_V1:
        raise ValueError("mcp.json declares an unsupported schema or top-level shape")
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        raise TypeError("MCP server list must be an object")
    servers = cast(dict[str, Any], servers)
    if len(servers) > MAX_SERVERS:
        raise ValueError("MCP server list is invalid or too large")
    result: list[PluginServer] = []
    for name, spec in servers.items():
        if not _SLUG.fullmatch(name) or not isinstance(spec, dict):
            raise ValueError("MCP server entry is invalid")
        spec = cast(dict[str, Any], spec)
        transport = spec.get("type")
        if transport == "stdio":
            if set(spec) - {"type", "command", "args", "env", "cwd"}:
                raise ValueError(f"MCP server {name} has unsupported stdio fields")
            command = spec.get("command")
            args = spec.get("args", [])
            env = spec.get("env", {})
            if not isinstance(command, str) or not command.strip():
                raise ValueError(f"MCP server {name} has invalid stdio command or arguments")
            if not isinstance(args, list):
                raise ValueError(f"MCP server {name} has invalid stdio command or arguments")
            args = cast(list[Any], args)
            if len(args) > 64 or any(not isinstance(arg, str) or len(arg) > 2048 for arg in args):
                raise ValueError(f"MCP server {name} has invalid stdio command or arguments")
            if not isinstance(env, dict):
                raise ValueError(f"MCP server {name} has invalid environment mapping")
            env = cast(dict[str, Any], env)
            if any(
                not _ENV.fullmatch(key) or not isinstance(value, str) for key, value in env.items()
            ):
                raise ValueError(f"MCP server {name} has invalid environment mapping")
            result.append(
                PluginServer(
                    name=name,
                    transport="stdio",
                    command_name=Path(command).name,
                    argument_count=len(args),
                    environment_names=sorted(env),
                )
            )
        elif transport == "streamable-http":
            if set(spec) - {"type", "url", "headers"}:
                raise ValueError(f"MCP server {name} has unsupported HTTP fields")
            endpoint = spec.get("url")
            if not isinstance(endpoint, str) or len(endpoint) > 2048:
                raise ValueError(f"MCP server {name} has invalid HTTP endpoint")
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError(f"MCP server {name} endpoint is not an allowed URL")
            if not isinstance(spec.get("headers", {}), dict):
                raise ValueError(f"MCP server {name} headers must be an object")
            headers = cast(dict[str, Any], spec.get("headers", {}))
            if any(
                not _HEADER.fullmatch(key)
                or not isinstance(value, str)
                or "\r" in value
                or "\n" in value
                for key, value in headers.items()
            ):
                raise ValueError(f"MCP server {name} has invalid HTTP headers")
            if parsed.scheme == "http":
                try:
                    loopback = (
                        parsed.hostname == "localhost"
                        or ipaddress.ip_address(parsed.hostname).is_loopback
                    )
                except ValueError:
                    loopback = False
                if not loopback:
                    raise ValueError(f"MCP server {name} cleartext endpoint must be loopback")
            if parsed.port == 0:
                raise ValueError(f"MCP server {name} endpoint cannot use port zero")
            port = f":{parsed.port}" if parsed.port is not None else ""
            safe_endpoint = f"{parsed.scheme}://{parsed.hostname}{port}/…"
            result.append(
                PluginServer(name=name, transport="streamable_http", endpoint=safe_endpoint)
            )
        else:
            raise ValueError(f"MCP server {name} uses an unsupported transport")
    return result


def _package(path: Path, root: Path, command_cache: dict[str, bool]) -> PluginSummary:
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
    if not isinstance(version, str) or len(version) > 128:
        raise ValueError("plugin.json version is invalid")
    if description is not None and (not isinstance(description, str) or len(description) > 2048):
        raise ValueError("plugin.json description is invalid")
    extensions = manifest.get("extensions", {})
    if not isinstance(extensions, dict):
        raise TypeError("plugin.json extensions must map namespaces to objects")
    extensions = cast(dict[str, Any], extensions)
    if any(not isinstance(value, dict) for value in extensions.values()):
        raise ValueError("plugin.json extensions must map namespaces to objects")
    return PluginSummary(
        name=name,
        version=version,
        description=description,
        path=str(package_root),
        skills=_skill_summaries(package_root, command_cache),
        servers=_servers(manifest, package_root),
    )


def discover_agent_plugins(directory: str) -> PluginDiscovery:
    """Inspect immediate package directories. No package code or skill contents are executed/read."""
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
        else sorted(islice(root.iterdir(), MAX_PACKAGES), key=lambda item: item.name)
    )
    packages: list[PluginSummary] = []
    diagnostics: list[PluginDiagnostic] = []
    command_cache: dict[str, bool] = {}
    for candidate in candidates[:MAX_PACKAGES]:
        if candidate.name.startswith("."):
            continue
        try:
            resolved = candidate.resolve(strict=True)
            if not _inside(resolved, root):
                raise ValueError("package directory escapes selected root")
            if not resolved.is_dir() or not (resolved / "plugin.json").exists():
                continue
            packages.append(_package(resolved, root, command_cache))
        except (OSError, RuntimeError, ValueError) as exc:
            diagnostics.append(PluginDiagnostic(path=candidate.name[:256], message=str(exc)[:300]))
    return PluginDiscovery(root=str(root), packages=packages, diagnostics=diagnostics)


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
    if skill_path.stat().st_size > 16_384:
        raise ValueError("skill file exceeds the 16 KiB procedure candidate limit")
    with skill_path.open("rb") as source:
        raw = source.read(16_385)
    if len(raw) > 16_384:
        raise ValueError("skill file exceeds the 16 KiB procedure candidate limit")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("skill file must be UTF-8 text") from exc
    if "\x00" in content:
        raise ValueError("skill file cannot contain NUL characters")
    description, allowed_tools, category, declared_requirements = _parse_skill_frontmatter(
        raw, skill.name
    )
    asset_rows: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    links: list[str] = _LOCAL_LINK.findall(content)
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
        relative = PurePosixPath(decoded_path)
        if (
            relative.is_absolute()
            or relative.as_posix() != decoded_path
            or "\\" in decoded_path
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("skill resource link must be a normalized relative path")
        resource_name = relative.as_posix()
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
        try:
            resource_bytes.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("linked skill resources must be UTF-8 text files") from exc
        media_type = mimetypes.guess_type(resource_name)[0] or "text/plain"
        if not media_type.startswith("text/") and media_type not in {
            "application/json",
            "application/yaml",
            "application/toml",
        }:
            raise ValueError("linked skill resource type is not a supported text format")
        asset_rows.append(
            {
                "path": resource_name,
                "content_base64": base64.b64encode(resource_bytes).decode("ascii"),
                "media_type": media_type,
                "sha256": hashlib.sha256(resource_bytes).hexdigest(),
            }
        )
    with (package_root / "plugin.json").open("rb") as manifest_source:
        manifest_bytes = manifest_source.read(MAX_MANIFEST_BYTES + 1)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError("plugin.json exceeds the package manifest size limit")
    return PluginSkillImport(
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
        prior_description, _, prior_category, _ = _parse_skill_frontmatter(
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
    if len(markdown.encode("utf-8")) > 16_384:
        raise ValueError("Exported SKILL.md exceeds the 16 KiB import limit.")
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

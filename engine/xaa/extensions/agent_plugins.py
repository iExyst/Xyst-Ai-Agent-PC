"""Bounded, read-only discovery for Agent Plugins v1 package directories."""

from __future__ import annotations

import ipaddress
import json
import re
from itertools import islice
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

MAX_PACKAGES = 128
MAX_SKILLS = 128
MAX_SERVERS = 64
MAX_MANIFEST_BYTES = 256 * 1024
_SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
PLUGIN_SCHEMA_V1 = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_V1 = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
_PLUGIN_NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_HEADER = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class PluginSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    path: str


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


def _skill_summaries(package_root: Path) -> list[PluginSkill]:
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
        summaries.append(
            PluginSkill(name=directory.name, path=skill_file.relative_to(package_root).as_posix())
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


def _package(path: Path, root: Path) -> PluginSummary:
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
        skills=_skill_summaries(package_root),
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
    for candidate in candidates[:MAX_PACKAGES]:
        if candidate.name.startswith("."):
            continue
        try:
            resolved = candidate.resolve(strict=True)
            if not _inside(resolved, root):
                raise ValueError("package directory escapes selected root")
            if not resolved.is_dir() or not (resolved / "plugin.json").exists():
                continue
            packages.append(_package(resolved, root))
        except (OSError, RuntimeError, ValueError) as exc:
            diagnostics.append(PluginDiagnostic(path=candidate.name[:256], message=str(exc)[:300]))
    return PluginDiscovery(root=str(root), packages=packages, diagnostics=diagnostics)

"""Durable user configuration and permission mapping for MCP servers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from xaa.storage.runs import RunStore, utc_now

MCP_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MCP_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class MCPServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: str = Field(min_length=1, max_length=80)
    transport: Literal["stdio", "streamable_http"] = "stdio"
    endpoint: str | None = Field(default=None, max_length=2048)
    command: str = Field(default="", max_length=512)
    args: list[str] = Field(default_factory=list, max_length=64)
    cwd: str | None = Field(default=None, max_length=2048)
    secret_env: list[str] = Field(default_factory=list, max_length=64)
    plugin_root: str | None = Field(default=None, max_length=2048)
    plugin_data: str | None = Field(default=None, max_length=2048)
    plugin_headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    plugin_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("name")
    @classmethod
    def trimmed_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("MCP server names must be plain text.")
        return value

    @field_validator("command")
    @classmethod
    def normalize_command(cls, value: str) -> str:
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            raise ValueError("MCP commands must be plain text.")
        return value

    @field_validator("args")
    @classmethod
    def validate_args(cls, value: list[str]) -> list[str]:
        if any(
            len(arg) > 2048
            or any(ord(char) < 32 for char in arg)
            or re.search(r"\$\{(?!PLUGIN_ROOT\}|PLUGIN_DATA\})", arg)
            for arg in value
        ):
            raise ValueError("MCP arguments must be bounded plain text strings.")
        return value

    @field_validator("secret_env")
    @classmethod
    def validate_secret_names(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            MCP_ENV_NAME.fullmatch(name) is None for name in value
        ):
            raise ValueError("MCP secret environment names must be unique identifiers.")
        return value

    @model_validator(mode="after")
    def validate_transport(self) -> MCPServerSpec:
        if self.transport == "stdio":
            if not self.command.strip() or self.endpoint is not None:
                raise ValueError("stdio MCP servers need a command and cannot set an endpoint.")
            if (self.plugin_root is None) != (self.plugin_data is None):
                raise ValueError("Plugin stdio servers require both package and data paths.")
            for path in (self.plugin_root, self.plugin_data):
                if path is not None and (
                    not path or not Path(path).is_absolute() or any(ord(char) < 32 for char in path)
                ):
                    raise ValueError("Plugin package and data paths must be absolute plain paths.")
        else:
            plugin_backed = self.plugin_root is not None and self.plugin_data is not None
            if (
                self.command
                or self.args
                or self.cwd
                or ((self.plugin_root is None) != (self.plugin_data is None))
                or (
                    not plugin_backed
                    and (self.plugin_root is not None or self.plugin_data is not None)
                )
            ):
                raise ValueError(
                    "Streamable HTTP MCP servers cannot set a command, arguments or working directory."
                )
            if self.endpoint is None:
                raise ValueError("Streamable HTTP MCP servers need an endpoint URL.")
            for path in (self.plugin_root, self.plugin_data):
                if path is not None and (
                    not path or not Path(path).is_absolute() or any(ord(char) < 32 for char in path)
                ):
                    raise ValueError("Plugin package and data paths must be absolute plain paths.")
            import ipaddress
            from urllib.parse import urlsplit

            parsed = urlsplit(self.endpoint)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or any(ord(char) < 32 for char in self.endpoint)
            ):
                raise ValueError(
                    "MCP endpoint must be an HTTP(S) URL without embedded credentials, query strings or fragments."
                )
            try:
                port = parsed.port
            except ValueError as error:
                raise ValueError("MCP endpoint URL contains an invalid port.") from error
            if port == 0:
                raise ValueError("MCP endpoint URL cannot use port zero.")
            if parsed.scheme == "http":
                host = parsed.hostname.lower()
                try:
                    loopback = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    loopback = host == "localhost"
                if not loopback:
                    raise ValueError("Remote MCP endpoints must use HTTPS.")
            if len(self.secret_env) > 1 and not plugin_backed:
                raise ValueError("Streamable HTTP supports one OS-stored bearer token name.")
            if self.plugin_headers and not plugin_backed:
                raise ValueError("Only package-backed HTTP servers can set custom header mappings.")
        if self.transport == "stdio" and self.plugin_headers:
            raise ValueError("stdio MCP servers cannot set HTTP header mappings.")
        header_names = [name.lower() for name in self.plugin_headers]
        if len(header_names) != len(set(header_names)) or any(
            not name or len(name) > 128 or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
            for name in self.plugin_headers
        ):
            raise ValueError("MCP header mapping names must be unique valid HTTP header names.")
        if any(
            not value
            or len(value) > 1024
            or not re.fullmatch(r"(?:[^$]|\$\{secret:[A-Za-z_][A-Za-z0-9_]{0,127}\})+", value)
            for value in self.plugin_headers.values()
        ):
            raise ValueError("MCP header mappings must reference valid credential names.")
        if self.plugin_root is None and any(
            "${PLUGIN_ROOT}" in value or "${PLUGIN_DATA}" in value
            for value in [self.command, *self.args, self.cwd or ""]
        ):
            raise ValueError("Package path placeholders require an installed MCP package.")
        if self.plugin_root is None and self.plugin_sha256 is not None:
            raise ValueError("Package source fingerprints require an installed MCP package.")
        return self


class MCPServerRecord(MCPServerSpec):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    created_at: str
    updated_at: str


class MCPDiscoveredTool(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MCPToolRecord(MCPDiscoveredTool):
    server_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    permission: Literal["none", "read", "write"] = "none"
    qualified_name: str
    updated_at: str


class MCPRegistry:
    def __init__(self, store: RunStore) -> None:
        self.store = store

    def create_server(self, spec: MCPServerSpec) -> MCPServerRecord:
        identity = uuid4().hex
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO mcp_servers (id,name,command,args,cwd,secret_env,created_at,updated_at,transport,endpoint,plugin_root,plugin_data,plugin_headers,plugin_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    spec.name,
                    spec.command,
                    json.dumps(spec.args),
                    spec.cwd,
                    json.dumps(spec.secret_env),
                    now,
                    now,
                    spec.transport,
                    spec.endpoint,
                    spec.plugin_root,
                    spec.plugin_data,
                    json.dumps(spec.plugin_headers),
                    spec.plugin_sha256,
                ),
            )
        return MCPServerRecord(id=identity, **spec.model_dump(), created_at=now, updated_at=now)

    def list_servers(self) -> list[MCPServerRecord]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM mcp_servers ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [self._server(dict(row)) for row in rows]

    def get_server(self, server_id: str) -> MCPServerRecord | None:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_servers WHERE id=?", (server_id,)
            ).fetchone()
        return self._server(dict(row)) if row is not None else None

    def delete_server(self, server_id: str) -> bool:
        with self.store.connection() as connection:
            cursor = connection.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,))
        return cursor.rowcount == 1

    def synchronize_tools(
        self, server_id: str, tools: list[MCPDiscoveredTool]
    ) -> list[MCPToolRecord]:
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute("SELECT 1 FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
                is None
            ):
                raise KeyError(server_id)
            current_names = {tool.name for tool in tools}
            for tool in tools:
                existing = connection.execute(
                    "SELECT schema_sha256,permission FROM mcp_server_tools WHERE server_id=? AND name=?",
                    (server_id, tool.name),
                ).fetchone()
                permission = (
                    str(existing["permission"])
                    if existing is not None and existing["schema_sha256"] == tool.schema_sha256
                    else "none"
                )
                connection.execute(
                    "INSERT INTO mcp_server_tools VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(server_id,name) DO UPDATE SET title=excluded.title,description=excluded.description,input_schema=excluded.input_schema,output_schema=excluded.output_schema,schema_sha256=excluded.schema_sha256,permission=excluded.permission,updated_at=excluded.updated_at",
                    (
                        server_id,
                        tool.name,
                        tool.title,
                        tool.description,
                        json.dumps(tool.input_schema, separators=(",", ":")),
                        json.dumps(tool.output_schema, separators=(",", ":"))
                        if tool.output_schema is not None
                        else None,
                        tool.schema_sha256,
                        permission,
                        now,
                    ),
                )
            if current_names:
                placeholders = ",".join("?" for _ in current_names)
                connection.execute(
                    f"DELETE FROM mcp_server_tools WHERE server_id=? AND name NOT IN ({placeholders})",
                    (server_id, *sorted(current_names)),
                )
            else:
                connection.execute("DELETE FROM mcp_server_tools WHERE server_id=?", (server_id,))
        return self.list_tools(server_id)

    def list_tools(self, server_id: str | None = None) -> list[MCPToolRecord]:
        with self.store.connection() as connection:
            if server_id is None:
                rows = connection.execute(
                    "SELECT t.*,s.name AS server_name FROM mcp_server_tools t JOIN mcp_servers s ON s.id=t.server_id ORDER BY s.name COLLATE NOCASE,t.name"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT t.*,s.name AS server_name FROM mcp_server_tools t JOIN mcp_servers s ON s.id=t.server_id WHERE t.server_id=? ORDER BY t.name",
                    (server_id,),
                ).fetchall()
        return [self._tool(dict(row)) for row in rows]

    def set_permission(
        self,
        server_id: str,
        name: str,
        permission: Literal["none", "read", "write"],
        schema_sha256: str,
    ) -> MCPToolRecord:
        if MCP_TOOL_NAME.fullmatch(name) is None:
            raise ValueError("Invalid MCP tool name.")
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE mcp_server_tools SET permission=?,updated_at=? WHERE server_id=? AND name=? AND schema_sha256=?",
                (permission, now, server_id, name, schema_sha256),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "MCP tool schema changed or tool is no longer available; rediscover it."
                )
        tool = next((item for item in self.list_tools(server_id) if item.name == name), None)
        if tool is None:
            raise KeyError(name)
        return tool

    @staticmethod
    def _server(row: dict[str, Any]) -> MCPServerRecord:
        return MCPServerRecord(
            id=row["id"],
            name=row["name"],
            command=row["command"],
            args=json.loads(row["args"]),
            cwd=row["cwd"],
            secret_env=json.loads(row["secret_env"]),
            transport=row.get("transport", "stdio"),
            endpoint=row.get("endpoint"),
            plugin_root=row.get("plugin_root"),
            plugin_data=row.get("plugin_data"),
            plugin_headers=json.loads(row.get("plugin_headers") or "{}"),
            plugin_sha256=row.get("plugin_sha256"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _tool(row: dict[str, Any]) -> MCPToolRecord:
        server_id = str(row["server_id"])
        return MCPToolRecord(
            server_id=server_id,
            name=row["name"],
            title=row["title"],
            description=row["description"],
            input_schema=json.loads(row["input_schema"]),
            output_schema=json.loads(row["output_schema"]) if row["output_schema"] else None,
            schema_sha256=row["schema_sha256"],
            permission=row["permission"],
            qualified_name=(f"mcp_{server_id}_{re.sub(r'[^A-Za-z0-9_-]', '_', row['name'])[:24]}"),
            updated_at=row["updated_at"],
        )

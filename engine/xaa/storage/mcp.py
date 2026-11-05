"""Durable user configuration and permission mapping for MCP stdio servers."""

import json
import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from xaa.storage.runs import RunStore, utc_now

MCP_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
MCP_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class MCPServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=512)
    args: list[str] = Field(default_factory=list, max_length=64)
    cwd: str | None = Field(default=None, max_length=2048)
    secret_env: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("name", "command")
    @classmethod
    def trimmed_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("MCP server names and commands must be plain text.")
        return value

    @field_validator("args")
    @classmethod
    def validate_args(cls, value: list[str]) -> list[str]:
        if any(len(arg) > 2048 or any(ord(char) < 32 for char in arg) for arg in value):
            raise ValueError("MCP arguments must be bounded plain text strings.")
        return value

    @field_validator("secret_env")
    @classmethod
    def validate_secret_names(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            MCP_ENV_NAME.fullmatch(name) is None for name in value
        ):
            raise ValueError("MCP secret environment names must be unique uppercase identifiers.")
        return value


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
                "INSERT INTO mcp_servers VALUES (?,?,?,?,?,?,?,?)",
                (
                    identity,
                    spec.name,
                    spec.command,
                    json.dumps(spec.args),
                    spec.cwd,
                    json.dumps(spec.secret_env),
                    now,
                    now,
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

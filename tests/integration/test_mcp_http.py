from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError
from xaa.runtime.mcp_manager import MCPManager
from xaa.storage.mcp import MCPServerSpec
from xaa.storage.runs import RunStore


class MemoryCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, profile: str = "lm-studio") -> str | None:
        return self.values.get(profile)

    def set(self, token: str, profile: str = "lm-studio") -> None:
        self.values[profile] = token

    def delete(self, profile: str = "lm-studio") -> None:
        self.values.pop(profile, None)


@pytest.mark.asyncio
async def test_streamable_http_discovery_auth_call_and_disconnect(workspace_tmp_path: Path):
    mcp = FastMCP("HTTP fixture")

    @mcp.tool()
    def add(left: int, right: int) -> int:
        """Add two numbers in the HTTP fixture."""
        return left + right

    app = mcp.streamable_http_app()
    captured_auth: list[str | None] = []

    async def capture_auth(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            token = headers.get(b"authorization")
            if token is not None:
                captured_auth.append(token.decode())
        await app(scope, receive, send)

    host = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    host.bind(("127.0.0.1", 0))
    port = host.getsockname()[1]
    config = uvicorn.Config(capture_auth, host="127.0.0.1", port=port, log_level="critical")
    web_server = uvicorn.Server(config)
    serving = asyncio.create_task(web_server.serve(sockets=[host]))
    store = RunStore(workspace_tmp_path / "mcp-http.sqlite3")
    credentials = MemoryCredentials()
    manager = MCPManager(store, credentials)  # type: ignore[arg-type]
    try:
        async with asyncio.timeout(5):
            while not web_server.started:
                await asyncio.sleep(0.01)
        record = manager.registry.create_server(
            MCPServerSpec(
                name="HTTP fixture",
                transport="streamable_http",
                endpoint=f"http://127.0.0.1:{port}/mcp",
                secret_env=["MCP_TOKEN"],
            )
        )
        credentials.set("fixture-bearer-token", f"mcp:{record.id}:MCP_TOKEN")
        tools = await manager.connect_server(record.id)
        assert [tool.name for tool in tools] == ["add"]
        tool = tools[0]
        assert tool.permission == "none"
        manager.registry.set_permission(record.id, "add", "read", tool.schema_sha256)
        result = await manager.call_tool(tool.qualified_name, {"left": 19, "right": 23})
        assert result["status"] == "ok"
        assert result["trust"] == "untrusted_mcp_output"
        assert result["structured_content"] == {"result": 42}
        assert captured_auth
        assert set(captured_auth) == {"Bearer fixture-bearer-token"}
        assert await manager.disconnect_server(record.id)
        assert manager.server_status(record.id)[0] == "disconnected"
    finally:
        await manager.close()
        web_server.should_exit = True
        await serving


def test_mcp_http_endpoint_policy_and_legacy_defaults():
    with pytest.raises(ValidationError, match="HTTPS"):
        MCPServerSpec(
            name="remote insecure",
            transport="streamable_http",
            endpoint="http://example.com/mcp",
        )
    with pytest.raises(ValidationError, match="credentials"):
        MCPServerSpec(
            name="embedded secret",
            transport="streamable_http",
            endpoint="https://user:pass@example.com/mcp",
        )
    with pytest.raises(ValidationError, match="query strings"):
        MCPServerSpec(
            name="query token",
            transport="streamable_http",
            endpoint="https://example.com/mcp?access_token=secret",
        )
    assert MCPServerSpec(name="legacy", command="python").transport == "stdio"

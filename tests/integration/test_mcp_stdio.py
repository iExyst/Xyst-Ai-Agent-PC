from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.types import CallToolResult, TextContent
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.mcp_manager import (
    MCP_RESULT_BYTES,
    MCPInvocationError,
    MCPManager,
    _result_payload,
)
from xaa.storage.mcp import MCPRegistry, MCPServerSpec
from xaa.storage.runs import RunStore

TOKEN = "a-test-token-that-is-long-enough-to-pass-validation"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py"


class MemoryCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, profile: str = "lm-studio") -> str | None:
        return self.values.get(profile)

    def set(self, token: str, profile: str = "lm-studio") -> None:
        self.values[profile] = token

    def delete(self, profile: str = "lm-studio") -> None:
        self.values.pop(profile, None)


def test_write_error_and_output_overflow_are_unknown_outcomes():
    server_error = _result_payload(
        CallToolResult(content=[TextContent(type="text", text="partial failure")], isError=True),
        write=True,
    )
    assert server_error["status"] == "unknown_outcome"
    assert "reconcile" in server_error["error"]

    oversized = CallToolResult(
        content=[TextContent(type="text", text="x" * MCP_RESULT_BYTES)], isError=False
    )
    assert _result_payload(oversized, write=True)["status"] == "unknown_outcome"
    assert _result_payload(oversized, write=False)["status"] == "output_limit"


@pytest.mark.asyncio
async def test_stdio_discovery_permission_call_and_disconnect(workspace_tmp_path: Path):
    store = RunStore(workspace_tmp_path / "mcp.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(name="Fixture", command=sys.executable, args=[str(FIXTURE)])
    )
    manager = MCPManager(store, None)
    try:
        tools = await manager.connect_server(server.id)
        assert [tool.name for tool in tools] == ["add"]
        tool = tools[0]
        assert tool.permission == "none"
        assert manager.tools_for_run(set()) == []

        registry.set_permission(server.id, "add", "write", tool.schema_sha256)
        qualified = registry.list_tools(server.id)[0].qualified_name
        assert manager.tools_for_run(set()) == []
        assert [item["function"]["name"] for item in manager.tools_for_run({qualified})] == [
            qualified
        ]
        result = await manager.call_tool(qualified, {"left": 20, "right": 22})
        assert result["status"] == "ok"
        assert result["trust"] == "untrusted_mcp_output"
        assert result["structured_content"] == {"result": 42}
        changed_schema = tool.model_copy(update={"schema_sha256": "a" * 64})
        changed = registry.synchronize_tools(server.id, [changed_schema])[0]
        assert changed.permission == "none"
        assert manager.tools_for_run({qualified}) == []
        with pytest.raises(MCPInvocationError, match="permission or schema"):
            await manager.call_tool(qualified, {"left": 1, "right": 1})
    finally:
        await manager.close()
    assert manager.tools_for_run(set()) == []


def test_authenticated_mcp_api_connect_and_permission(workspace_tmp_path: Path):
    store = RunStore(workspace_tmp_path / "mcp-api.sqlite3")
    provider = LMStudioProvider()
    credentials = MemoryCredentials()
    with TestClient(
        create_app(provider, SecretStr(TOKEN), store=store, credential_store=credentials)  # type: ignore[arg-type]
    ) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        created = client.post(
            "/mcp/servers",
            headers=headers,
            json={
                "name": "Fixture",
                "command": sys.executable,
                "args": [str(FIXTURE)],
                "secret_env": ["API_TOKEN"],
            },
        )
        assert created.status_code == 201
        server_id = created.json()["id"]
        missing_secret = client.post(f"/mcp/servers/{server_id}/connect", headers=headers)
        assert missing_secret.status_code == 502
        assert "Set the configured API_TOKEN credential" in missing_secret.json()["detail"]
        saved_secret = client.put(
            f"/mcp/servers/{server_id}/secrets/API_TOKEN",
            headers=headers,
            json={"value": "fixture-secret-value"},
        )
        assert saved_secret.json() == {"configured": True}
        connected = client.post(f"/mcp/servers/{server_id}/connect", headers=headers)
        assert connected.status_code == 200
        tool = connected.json()["tools"][0]
        denied = client.put(
            f"/mcp/servers/{server_id}/tools/add/permission",
            headers=headers,
            json={"permission": "read", "schema_sha256": "0" * 64},
        )
        assert denied.status_code == 409
        allowed = client.put(
            f"/mcp/servers/{server_id}/tools/add/permission",
            headers=headers,
            json={"permission": "read", "schema_sha256": tool["schema_sha256"]},
        )
        assert allowed.status_code == 200
        assert allowed.json()["permission"] == "read"
        run = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Use the explicitly selected MCP tool",
                "approved_mcp_tools": [tool["qualified_name"]],
            },
        )
        assert run.status_code == 201
        assert client.get(f"/runs/{run.json()['id']}", headers=headers).json()[
            "approved_mcp_tools"
        ] == [tool["qualified_name"]]
        listing = client.get("/mcp/servers", headers=headers)
        assert listing.json()[0]["state"] == "connected"
        assert "fixture-secret-value" not in listing.text
        assert credentials.get(f"mcp:{server_id}:API_TOKEN") == "fixture-secret-value"
        assert (
            client.post(f"/mcp/servers/{server_id}/disconnect", headers=headers).status_code == 200
        )
        assert client.delete(f"/mcp/servers/{server_id}", headers=headers).status_code == 204
        assert credentials.get(f"mcp:{server_id}:API_TOKEN") is None

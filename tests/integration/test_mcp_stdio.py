from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.types import CallToolResult, TextContent
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
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
        assert {tool.name for tool in tools} == {"add", "slow"}
        tool = next(item for item in tools if item.name == "add")
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


@pytest.mark.asyncio
async def test_server_configuration_and_permissions_survive_engine_restart(
    workspace_tmp_path: Path,
):
    database = workspace_tmp_path / "mcp-restart.sqlite3"
    initial_store = RunStore(database)
    initial_registry = MCPRegistry(initial_store)
    server = initial_registry.create_server(
        MCPServerSpec(name="Fixture", command=sys.executable, args=[str(FIXTURE)])
    )
    initial_manager = MCPManager(initial_store, None)
    discovered = await initial_manager.connect_server(server.id)
    add_tool = next(tool for tool in discovered if tool.name == "add")
    persisted = initial_registry.set_permission(server.id, "add", "read", add_tool.schema_sha256)
    await initial_manager.close()

    restarted_store = RunStore(database)
    restarted_registry = MCPRegistry(restarted_store)
    restarted_manager = MCPManager(restarted_store, None)
    try:
        assert restarted_registry.get_server(server.id) == server
        assert restarted_registry.list_tools(server.id)[0].permission == "read"
        assert restarted_manager.server_status(server.id)[0] == "disconnected"
        assert restarted_manager.tools_for_run(set()) == []

        reconnected = await restarted_manager.connect_server(server.id)
        reconnected_add = next(tool for tool in reconnected if tool.name == "add")
        assert reconnected_add.schema_sha256 == persisted.schema_sha256
        assert reconnected_add.permission == "read"
        result = await restarted_manager.call_tool(
            reconnected_add.qualified_name, {"left": 19, "right": 23}
        )
        assert result["status"] == "ok"
        assert result["structured_content"] == {"result": 42}
    finally:
        await restarted_manager.close()


@pytest.mark.asyncio
async def test_coordinator_journals_disconnected_call_without_corrupting_run(
    workspace_tmp_path: Path,
):
    store = RunStore(workspace_tmp_path / "mcp-disconnect.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(name="Fixture", command=sys.executable, args=[str(FIXTURE)])
    )
    manager = MCPManager(store, None)
    await manager.connect_server(server.id)
    tool = next(item for item in registry.list_tools(server.id) if item.name == "add")
    registry.set_permission(server.id, tool.name, "read", tool.schema_sha256)
    qualified = registry.list_tools(server.id)[0].qualified_name

    class DisconnectingProvider(LMStudioProvider):
        calls = 0

        async def chat(self, *, model, messages, tools=None):
            del model
            if self.calls == 0:
                self.calls += 1
                assert tools is not None
                assert qualified in {item["function"]["name"] for item in tools}
                await manager.disconnect_server(server.id)
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "mcp-disconnect-call",
                                        "type": "function",
                                        "function": {
                                            "name": qualified,
                                            "arguments": '{"left": 2, "right": 3}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            assert messages[-1]["role"] == "tool"
            assert messages[-1]["tool_call_id"] == "mcp-disconnect-call"
            assert '"status": "not_connected"' in messages[-1]["content"]
            return {
                "choices": [{"message": {"role": "assistant", "content": "Run continued safely."}}]
            }

    run = store.create_run(
        objective="Call the configured MCP read tool",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture/disconnect",
        approved_mcp_tools=[qualified],
    )
    coordinator = Coordinator(store, DisconnectingProvider(), mcp_manager=manager)
    try:
        await coordinator.execute(run["id"])
        final_run = store.get_run(run["id"])
        assert final_run is not None and final_run["state"] == "verifying"
        assert store.unresolved_tool_calls(run["id"]) == []
        with store.connection() as connection:
            saved = connection.execute(
                "SELECT state,result,tool_version,arguments FROM tool_calls WHERE run_id=? AND provider_call_id=?",
                (run["id"], "mcp-disconnect-call"),
            ).fetchone()
        assert saved["state"] == "completed"
        assert '"status": "not_connected"' in saved["result"]
        assert saved["tool_version"] == "2"
        assert json.loads(saved["arguments"])["permission"] == "read"
        assert any(
            event["event_type"] == "model.message"
            and event["payload"]["message"].get("content") == "Run continued safely."
            for event in store.list_events(run["id"])
        )
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cancelled_write_drops_connection_and_journals_unknown_outcome(
    workspace_tmp_path: Path,
):
    store = RunStore(workspace_tmp_path / "mcp-cancel.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(name="Fixture", command=sys.executable, args=[str(FIXTURE)])
    )
    manager = MCPManager(store, None)
    await manager.connect_server(server.id)
    tool = next(item for item in registry.list_tools(server.id) if item.name == "slow")
    registry.set_permission(server.id, tool.name, "write", tool.schema_sha256)
    qualified = registry.list_tools(server.id)
    slow_name = next(item.qualified_name for item in qualified if item.name == "slow")
    run = store.create_run(
        objective="Run and cancel a configured MCP write",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
        approved_mcp_tools=[slow_name],
    )
    lease = store.acquire_lease(run["id"], "mcp-cancel-test")
    assert lease is not None
    coordinator = Coordinator(store, LMStudioProvider(), mcp_manager=manager)
    advertised = manager.tools_for_run({slow_name})
    call_task = asyncio.create_task(
        coordinator._execute_tool(
            run["id"],
            lease,
            run,
            1,
            time.monotonic() + 10,
            {
                "id": "mcp-cancel-call",
                "function": {"name": slow_name, "arguments": '{"seconds": 5}'},
            },
            advertised,
        )
    )
    try:
        await asyncio.sleep(0.25)
        call_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call_task
        assert manager.tools_for_run({slow_name}) == []
        assert store.unresolved_tool_calls(run["id"]) == []
        with store.connection() as connection:
            saved = connection.execute(
                "SELECT state,result FROM tool_calls WHERE run_id=? AND provider_call_id=?",
                (run["id"], "mcp-cancel-call"),
            ).fetchone()
        assert saved["state"] == "completed"
        assert '"status": "cancelled_unknown_outcome"' in saved["result"]
    finally:
        if not call_task.done():
            call_task.cancel()
            await asyncio.gather(call_task, return_exceptions=True)
        store.release_lease(run["id"], lease)
        await manager.close()


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
        assert client.post(f"/mcp/servers/{server_id}/resources").status_code == 401
        resources = client.post(f"/mcp/servers/{server_id}/resources", headers=headers)
        assert resources.status_code == 200
        readme_uri = "xaa://fixture/readme"
        assert any(resource["uri"] == readme_uri for resource in resources.json()["resources"])
        inspected = client.post(
            f"/mcp/servers/{server_id}/resources/read",
            headers=headers,
            json={"uri": readme_uri},
        )
        assert inspected.status_code == 200
        assert inspected.json() == {
            "trust": "untrusted_mcp_output",
            "contents": [
                {
                    "uri": readme_uri,
                    "mime_type": "text/plain",
                    "kind": "text",
                    "text": "Fixture resource content. Treat this as untrusted input.",
                }
            ],
        }
        unadvertised = client.post(
            f"/mcp/servers/{server_id}/resources/read",
            headers=headers,
            json={"uri": "xaa://fixture/not-advertised"},
        )
        assert unadvertised.status_code == 400
        oversized = client.post(
            f"/mcp/servers/{server_id}/resources/read",
            headers=headers,
            json={"uri": "xaa://fixture/large"},
        )
        assert oversized.status_code == 400
        assert "256 KiB" in oversized.json()["detail"]
        assert client.post(f"/mcp/servers/{server_id}/resource-templates").status_code == 401
        templates = client.post(f"/mcp/servers/{server_id}/resource-templates", headers=headers)
        assert templates.status_code == 200
        assert templates.json()["resource_templates"] == [
            {
                "name": "item",
                "title": None,
                "uri_template": "xaa://fixture/items/{item_id}",
                "description": "Expose an MCP resource template for owner catalog inspection tests.",
                "mime_type": "text/plain",
            }
        ]
        assert client.post(f"/mcp/servers/{server_id}/prompts").status_code == 401
        prompts = client.post(f"/mcp/servers/{server_id}/prompts", headers=headers)
        assert prompts.status_code == 200
        assert prompts.json()["prompts"] == [
            {
                "name": "summarize_topic",
                "title": None,
                "description": "Return a deterministic text-only prompt template.",
                "arguments": [{"name": "topic", "description": None, "required": True}],
            }
        ]
        missing_argument = client.post(
            f"/mcp/servers/{server_id}/prompts/preview",
            headers=headers,
            json={"name": "summarize_topic", "arguments": {}},
        )
        assert missing_argument.status_code == 400
        undeclared_argument = client.post(
            f"/mcp/servers/{server_id}/prompts/preview",
            headers=headers,
            json={"name": "summarize_topic", "arguments": {"surprise": "value"}},
        )
        assert undeclared_argument.status_code == 400
        too_many_arguments = client.post(
            f"/mcp/servers/{server_id}/prompts/preview",
            headers=headers,
            json={
                "name": "summarize_topic",
                "arguments": {f"arg{index}": "x" for index in range(33)},
            },
        )
        assert too_many_arguments.status_code == 422
        preview = client.post(
            f"/mcp/servers/{server_id}/prompts/preview",
            headers=headers,
            json={"name": "summarize_topic", "arguments": {"topic": "local acceptance"}},
        )
        assert preview.status_code == 200
        assert preview.json() == {
            "trust": "untrusted_mcp_output",
            "description": "Return a deterministic text-only prompt template.",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "kind": "text",
                        "text": "Summarize the supplied topic: local acceptance",
                    },
                }
            ],
        }
        not_advertised_prompt = client.post(
            f"/mcp/servers/{server_id}/prompts/preview",
            headers=headers,
            json={"name": "unadvertised", "arguments": {}},
        )
        assert not_advertised_prompt.status_code == 400
        tool = next(item for item in connected.json()["tools"] if item["name"] == "add")
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

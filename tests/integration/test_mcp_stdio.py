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
from xaa.extensions.agent_plugins import plugin_source_sha256
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.mcp_manager import (
    MCP_RESULT_BYTES,
    MCPConnectionError,
    MCPInvocationError,
    MCPManager,
    _ConnectedServer,
    _result_payload,
    _validate_output_schema,
)
from xaa.storage.mcp import MCPRegistry, MCPServerRecord, MCPServerSpec, MCPToolRecord
from xaa.storage.runs import RunStore

TOKEN = "a-test-token-that-is-long-enough-to-pass-validation"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py"
DISCONNECT_FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_disconnect_server.py"


@pytest.mark.asyncio
async def test_connection_that_dies_immediately_after_discovery_is_not_registered(
    workspace_tmp_path: Path,
):
    store = RunStore(workspace_tmp_path / "mcp-immediate-disconnect.sqlite3")
    server = MCPRegistry(store).create_server(
        MCPServerSpec(name="Fixture", command=sys.executable, args=[str(FIXTURE)])
    )

    class DisconnectAfterDiscoveryManager(MCPManager):
        async def _own_connection(
            self,
            server: MCPServerRecord,
            ready: asyncio.Future[tuple[_ConnectedServer, list[MCPToolRecord]]],
        ) -> None:
            owner = asyncio.current_task()
            if owner is None:
                raise RuntimeError("Fixture connection task is missing.")
            connection = _ConnectedServer(
                server,
                client=None,  # type: ignore[arg-type]  # The task ends before any client operation.
                session=None,  # type: ignore[arg-type]  # The task ends before any session operation.
                tools={},
                resources={},
                prompts={},
                resource_templates={},
                expanded_resource_uris=set(),
                shutdown=asyncio.Event(),
                owner_task=owner,
                notifications=asyncio.Queue(maxsize=128),
            )
            ready.set_result((connection, []))

    manager = DisconnectAfterDiscoveryManager(store, None)
    with pytest.raises(MCPConnectionError, match="disconnected during connection setup"):
        await manager.connect_server(server.id)
    assert manager.server_status(server.id)[0] == "error"
    assert server.id not in manager._connected
    await manager.close()


def test_plugin_parameters_expand_paths_and_reject_package_escape(workspace_tmp_path: Path) -> None:
    package = workspace_tmp_path / "installed-package"
    package.mkdir()
    data = workspace_tmp_path / "plugin-data" / "sample"
    registry = MCPRegistry(RunStore(workspace_tmp_path / "plugin-parameters.sqlite3"))
    server = registry.create_server(
        MCPServerSpec(
            name="Package helper",
            command="./bin/server.exe",
            args=["${PLUGIN_ROOT}/config.json", "${PLUGIN_DATA}/state.json"],
            cwd="${PLUGIN_DATA}",
            plugin_root=str(package),
            plugin_data=str(data),
        )
    )
    parameters = MCPManager(registry.store, None)._parameters(server)
    assert parameters.command == str(package / "bin" / "server.exe")
    assert parameters.args == [str(package / "config.json"), str(data / "state.json")]
    assert parameters.cwd == str(data.resolve())
    assert parameters.env == {"PLUGIN_ROOT": str(package), "PLUGIN_DATA": str(data.resolve())}

    escaping = registry.create_server(
        MCPServerSpec(
            name="Escaping package helper",
            command="./../outside.exe",
            plugin_root=str(package),
            plugin_data=str(data),
        )
    )
    with pytest.raises(MCPConnectionError, match="escapes its package"):
        MCPManager(registry.store, None)._parameters(escaping)


@pytest.mark.asyncio
async def test_changed_plugin_package_is_refused_before_launch(workspace_tmp_path: Path) -> None:
    package = workspace_tmp_path / "changed-package"
    package.mkdir()
    source = package / "server.py"
    source.write_text("print('reviewed')", encoding="utf-8")
    store = RunStore(workspace_tmp_path / "mcp-changed-package.sqlite3")
    server = MCPRegistry(store).create_server(
        MCPServerSpec(
            name="Changed package",
            command=sys.executable,
            args=["-c", "raise SystemExit('package launched')"],
            plugin_root=str(package),
            plugin_data=str(workspace_tmp_path / "plugin-data"),
            plugin_sha256=plugin_source_sha256(package),
        )
    )
    source.write_text("print('changed after review')", encoding="utf-8")
    manager = MCPManager(store, None)
    with pytest.raises(MCPConnectionError, match="changed after it was added"):
        await manager.connect_server(server.id)
    assert manager.server_status(server.id) == (
        "error",
        "The installed MCP package changed after it was added. Review it, then remove and re-add the integration before connecting.",
    )
    await manager.close()


@pytest.mark.asyncio
async def test_changed_plugin_package_disconnects_before_next_tool_call(
    workspace_tmp_path: Path,
) -> None:
    package = workspace_tmp_path / "live-package"
    package.mkdir()
    source = package / "server.py"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    store = RunStore(workspace_tmp_path / "mcp-live-package.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(
            name="Live package",
            command=sys.executable,
            args=["${PLUGIN_ROOT}/server.py"],
            plugin_root=str(package),
            plugin_data=str(workspace_tmp_path / "plugin-data"),
            plugin_sha256=plugin_source_sha256(package),
        )
    )
    manager = MCPManager(store, None)
    try:
        tools = await manager.connect_server(server.id)
        tool = next(item for item in tools if item.name == "add")
        registry.set_permission(server.id, tool.name, "read", tool.schema_sha256)
        tool = next(item for item in registry.list_tools(server.id) if item.name == "add")
        source.write_text(source.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        with pytest.raises(MCPInvocationError, match="changed after it was added"):
            await manager.call_tool(tool.qualified_name, {"left": 1, "right": 2}, require_read=True)
        assert manager.server_status(server.id)[0] == "error"
        source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        await manager.connect_server(server.id)
        source.write_text(
            source.read_text(encoding="utf-8") + "\n# changed again\n", encoding="utf-8"
        )
        with pytest.raises(MCPConnectionError, match="changed after it was added"):
            await manager.connect_server(server.id)
        assert server.id not in manager._connected
        assert manager.server_status(server.id)[0] == "error"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_owner_reconnects_after_live_stdio_transport_loss(
    workspace_tmp_path: Path,
):
    store = RunStore(workspace_tmp_path / "mcp-live-reconnect.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(
            name="Disconnect fixture",
            command=sys.executable,
            args=[str(DISCONNECT_FIXTURE)],
        )
    )
    manager = MCPManager(store, None)
    try:
        await manager.connect_server(server.id)
        terminate = next(
            tool for tool in registry.list_tools(server.id) if tool.name == "terminate_transport"
        )
        registry.set_permission(server.id, terminate.name, "read", terminate.schema_sha256)
        with pytest.raises(MCPInvocationError) as failure:
            await manager.call_tool(terminate.qualified_name, {})
        assert failure.value.status == "unknown_outcome"
        assert manager.server_status(server.id)[0] == "error"
        assert server.id not in manager._connected

        reconnected = await manager.connect_server(server.id)
        assert {tool.name for tool in reconnected} == {"add", "terminate_transport"}
        restored = next(tool for tool in reconnected if tool.name == "terminate_transport")
        assert restored.permission == "read"
        add = next(tool for tool in reconnected if tool.name == "add")
        assert add.permission == "none"
        registry.set_permission(server.id, add.name, "read", add.schema_sha256)
        result = await manager.call_tool(add.qualified_name, {"left": 19, "right": 23})
        assert result["status"] == "ok"
        assert result["structured_content"] == {"result": 42}
    finally:
        await manager.close()


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
        CallToolResult(content=[TextContent(type="text", text="partial failure")], is_error=True),
        write=True,
    )
    assert server_error["status"] == "unknown_outcome"
    assert "reconcile" in server_error["error"]

    oversized = CallToolResult(
        content=[TextContent(type="text", text="x" * MCP_RESULT_BYTES)], is_error=False
    )
    assert _result_payload(oversized, write=True)["status"] == "unknown_outcome"
    assert _result_payload(oversized, write=False)["status"] == "output_limit"


def test_mcp_structured_output_is_checked_against_declared_schema():
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    valid = CallToolResult(content=[], structured_content={"answer": 42})
    invalid = CallToolResult(content=[], structured_content={"answer": "42"})
    assert _result_payload(valid, write=False, output_schema=schema)["status"] == "ok"
    assert _result_payload(invalid, write=False, output_schema=schema)["status"] == "output_invalid"
    write_failure = _result_payload(invalid, write=True, output_schema=schema)
    assert write_failure["status"] == "unknown_outcome"
    assert "reconcile" in write_failure["error"]

    with pytest.raises(MCPConnectionError, match="output schema exceeded"):
        _validate_output_schema({"type": "object", "description": "x" * MCP_RESULT_BYTES})


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
async def test_stdio_resource_subscription_delivers_bounded_untrusted_update(
    workspace_tmp_path: Path,
):
    store = RunStore(workspace_tmp_path / "mcp-subscription.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(name="Fixture", command=sys.executable, args=[str(FIXTURE)])
    )
    manager = MCPManager(store, None)
    try:
        tools = await manager.connect_server(server.id)
        slow = next(tool for tool in tools if tool.name == "slow")
        registry.set_permission(server.id, "slow", "read", slow.schema_sha256)
        await manager.list_resources(server.id)
        subscription = await manager.set_resource_subscription(
            server.id, "xaa://fixture/readme", subscribe=True
        )
        assert subscription["subscribed"] is True
        result = await manager.call_tool(slow.qualified_name, {"seconds": 0})
        assert result["status"] == "ok"
        await asyncio.sleep(0.05)
        updates = await manager.drain_resource_notifications(server.id)
        assert updates == {
            "trust": "untrusted_mcp_output",
            "notifications": [
                {
                    "kind": "resource_updated",
                    "uri": "xaa://fixture/readme",
                    "trust": "untrusted_mcp_output",
                }
            ],
        }
        await manager.set_resource_subscription(server.id, "xaa://fixture/readme", subscribe=False)
        assert (await manager.drain_resource_notifications(server.id))["notifications"] == []
    finally:
        await manager.close()


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
        assert (
            client.post(
                f"/mcp/servers/{server_id}/resources/subscription",
                json={"uri": readme_uri, "subscribe": True},
            ).status_code
            == 401
        )
        subscription = client.post(
            f"/mcp/servers/{server_id}/resources/subscription",
            headers=headers,
            json={"uri": readme_uri, "subscribe": True},
        )
        assert subscription.status_code == 200
        assert subscription.json()["subscribed"] is True
        notifications = client.post(
            f"/mcp/servers/{server_id}/resources/notifications", headers=headers
        )
        assert notifications.status_code == 200
        assert notifications.json() == {"notifications": [], "trust": "untrusted_mcp_output"}
        unsubscribed = client.post(
            f"/mcp/servers/{server_id}/resources/subscription",
            headers=headers,
            json={"uri": readme_uri, "subscribe": False},
        )
        assert unsubscribed.status_code == 200
        assert unsubscribed.json()["subscribed"] is False
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
                "variables": ["item_id"],
                "description": "Expose an MCP resource template for owner catalog inspection tests.",
                "mime_type": "text/plain",
            }
        ]
        template_read = client.post(
            f"/mcp/servers/{server_id}/resource-templates/read",
            headers=headers,
            json={"name": "item", "arguments": {"item_id": "sample-42"}},
        )
        assert template_read.json() == {
            "trust": "untrusted_mcp_output",
            "contents": [
                {
                    "uri": "xaa://fixture/items/sample-42",
                    "mime_type": "text/plain",
                    "kind": "text",
                    "text": "Fixture item: sample-42",
                }
            ],
        }
        missing_template_argument = client.post(
            f"/mcp/servers/{server_id}/resource-templates/read",
            headers=headers,
            json={"name": "item", "arguments": {}},
        )
        assert missing_template_argument.status_code == 400
        oversized_template_arguments = client.post(
            f"/mcp/servers/{server_id}/resource-templates/read",
            headers=headers,
            json={
                "name": "item",
                "arguments": {f"arg{index}": "x" for index in range(33)},
            },
        )
        assert oversized_template_arguments.status_code == 422
        oversized_template_value = client.post(
            f"/mcp/servers/{server_id}/resource-templates/read",
            headers=headers,
            json={"name": "item", "arguments": {"item_id": "x" * 8193}},
        )
        assert oversized_template_value.status_code == 422
        unadvertised_template = client.post(
            f"/mcp/servers/{server_id}/resource-templates/read",
            headers=headers,
            json={"name": "other", "arguments": {"item_id": "sample-42"}},
        )
        assert unadvertised_template.status_code == 400
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

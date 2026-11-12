from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from mcp.client.stdio import StdioServerParameters
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
from xaa.runtime.mcp_stdio import (
    MCP_MAX_STDIO_FRAME_BYTES,
    MCPStdioFrameLimitError,
    bounded_stdio_client,
)
from xaa.runtime.states import RunState
from xaa.storage.mcp import MCPRegistry, MCPServerRecord, MCPServerSpec, MCPToolRecord
from xaa.storage.runs import RunStore

TOKEN = "a-test-token-that-is-long-enough-to-pass-validation"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py"
DISCONNECT_FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_disconnect_server.py"
SAMPLING_FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_sampling_server.py"
MODERN_SAMPLING_FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_sampling_modern_server.py"
OVERSIZED_FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_oversized_stdio_server.py"


@pytest.mark.asyncio
async def test_stdio_transport_rejects_oversized_frame_before_json_parse(
    workspace_tmp_path: Path,
):
    cleanup_marker = workspace_tmp_path / "stdio-server-stopped.txt"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(OVERSIZED_FIXTURE)],
        env={"MCP_OVERSIZED_CLEANUP_MARKER": str(cleanup_marker)},
    )
    with pytest.raises(ExceptionGroup) as error_info:
        async with bounded_stdio_client(params):
            await asyncio.sleep(5)
    assert error_info.group_contains(
        MCPStdioFrameLimitError,
        match=str(MCP_MAX_STDIO_FRAME_BYTES),
    )
    server = MCPServerRecord(
        id="a" * 32,
        name="Oversized fixture",
        command=sys.executable,
        created_at="2026-11-12T00:00:00Z",
        updated_at="2026-11-12T00:00:00Z",
    )
    safe_message = object.__new__(MCPManager)._safe_error(error_info.value, server)
    assert f"{MCP_MAX_STDIO_FRAME_BYTES}-byte limit" in safe_message
    assert cleanup_marker.read_text(encoding="utf-8") == "stdin closed"


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol_mode", "sampling_fixture"),
    [("auto", MODERN_SAMPLING_FIXTURE), ("legacy", SAMPLING_FIXTURE)],
)
async def test_mcp_sampling_is_run_bound_text_only_and_included_in_shared_budget(
    workspace_tmp_path: Path,
    protocol_mode: Literal["auto", "legacy"],
    sampling_fixture: Path,
):
    store = RunStore(workspace_tmp_path / "mcp-sampling.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(
            name="Sampling fixture",
            command=sys.executable,
            args=[str(sampling_fixture)],
            protocol_mode=protocol_mode,
        )
    )
    manager = MCPManager(store, None)
    tools = await manager.connect_server(server.id)
    if protocol_mode == "auto":
        assert manager._connected[server.id].client.protocol_version == "2026-07-28"
    tool = next(item for item in tools if item.name == "sample_text")
    registry.set_permission(server.id, tool.name, "read", tool.schema_sha256)
    qualified = next(
        item.qualified_name for item in registry.list_tools(server.id) if item.name == "sample_text"
    )

    class SamplingProvider(LMStudioProvider):
        def __init__(self):
            super().__init__()
            self.sampling_messages = None
            self.sampling_tools = "unset"

        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None, max_output_tokens=None, **kwargs):
            if tools is None:
                self.sampling_messages = messages
                self.sampling_tools = tools
                assert max_output_tokens == 8
                content = "fixture sampling phrase"
                return {
                    "choices": [{"message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11},
                }
            if not any(item["role"] == "tool" for item in messages):
                assert any(item["function"]["name"] == qualified for item in tools)
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "sample-call",
                                        "type": "function",
                                        "function": {"name": qualified, "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
                }
            assert "fixture sampling phrase" in messages[-1]["content"]
            return {
                "choices": [{"message": {"role": "assistant", "content": "Sampling completed."}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }

    provider = SamplingProvider()
    run = store.create_run(
        objective="Run-only objective that must not enter the sampling prompt",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture/sampling-model",
        max_iterations=5,
        max_total_tokens=60,
        max_output_tokens=8,
        approved_mcp_sampling_servers=[server.id],
    )
    try:
        await Coordinator(store, provider, mcp_manager=manager).execute(run["id"])
        saved = store.get_run(run["id"])
        with store.connection() as connection:
            call_rows = connection.execute(
                "SELECT tool_name,state,result,error FROM tool_calls WHERE run_id=?",
                (run["id"],),
            ).fetchall()
        assert saved is not None and saved["state"] == RunState.VERIFYING, {
            "events": [event["event_type"] for event in store.list_events(run["id"])[-15:]],
            "unresolved": store.unresolved_tool_calls(run["id"]),
            "calls": [
                {
                    "tool": row["tool_name"],
                    "state": row["state"],
                    "result": json.loads(row["result"]),
                }
                for row in call_rows
            ],
        }
        assert saved["approved_mcp_sampling_servers"] == [server.id]
        assert provider.sampling_tools is None
        assert provider.sampling_messages is not None
        serialized_prompt = json.dumps(provider.sampling_messages)
        assert "Run-only objective" not in serialized_prompt
        events = store.list_events(run["id"])
        requested = next(
            event for event in events if event["event_type"] == "mcp.sampling.requested"
        )
        completed = next(
            event for event in events if event["event_type"] == "mcp.sampling.completed"
        )
        assert requested["payload"]["server_id"] == server.id
        assert requested["payload"]["max_output_tokens"] == 8
        assert requested["payload"]["trust"] == "untrusted_mcp_output"
        assert requested["payload"]["tool_call_id"] == "sample-call"
        assert completed["payload"]["content"] == "fixture sampling phrase"
        assert completed["payload"]["tool_call_id"] == "sample-call"
        assert store.inference_attempts(run["id"]) == 3
        assert store.token_usage(run["id"])["total_tokens"] == 29
        assert store.usage_report(run["id"])["outstanding_reservations"] == 0
        assert not any(
            message.get("content") == "fixture sampling phrase"
            for message in store.conversation_messages(run["id"])
        )
    finally:
        await provider.close()
        await manager.close()


@pytest.mark.asyncio
async def test_failed_mcp_sampling_is_measured_and_never_retried(
    workspace_tmp_path: Path,
):
    store = RunStore(workspace_tmp_path / "mcp-sampling-failure.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(
            name="Sampling failure fixture",
            command=sys.executable,
            args=[str(SAMPLING_FIXTURE)],
            protocol_mode="legacy",
        )
    )
    manager = MCPManager(store, None)
    tools = await manager.connect_server(server.id)
    tool = next(item for item in tools if item.name == "sample_text")
    registry.set_permission(server.id, tool.name, "read", tool.schema_sha256)
    qualified = next(
        item.qualified_name for item in registry.list_tools(server.id) if item.name == "sample_text"
    )

    class OversizedSamplingProvider(LMStudioProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, messages, tools=None, **kwargs):
            self.calls += 1
            if tools is None:
                assert kwargs["max_output_tokens"] == 100
                return {
                    "choices": [{"message": {"role": "assistant", "content": "x" * 16_001}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11},
                }
            assert self.calls == 1
            assert any(item["function"]["name"] == qualified for item in tools)
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "sample-failure-call",
                                    "type": "function",
                                    "function": {
                                        "name": qualified,
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
            }

    provider = OversizedSamplingProvider()
    run = store.create_run(
        objective="Do not repeat an MCP tool after its sampling response fails",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture/sampling-model",
        max_iterations=5,
        max_total_tokens=60,
        approved_mcp_sampling_servers=[server.id],
    )
    try:
        await Coordinator(store, provider, mcp_manager=manager).execute(run["id"])
        saved = store.get_run(run["id"])
        assert saved is not None and saved["state"] == RunState.BLOCKED
        assert provider.calls == 2
        assert store.inference_attempts(run["id"]) == 2
        assert store.token_usage(run["id"]) == {
            "prompt_tokens": 16,
            "completion_tokens": 6,
            "total_tokens": 22,
            "unmeasured_calls": 0,
        }
        events = store.list_events(run["id"])
        failed = next(event for event in events if event["event_type"] == "mcp.sampling.failed")
        assert failed["payload"]["tool_call_id"] == "sample-failure-call"
        assert failed["payload"]["usage"]["total_tokens"] == 11
        assert not any(event["event_type"] == "mcp.sampling.completed" for event in events)
        with store.connection() as connection:
            call = connection.execute(
                "SELECT result FROM tool_calls WHERE run_id=? AND provider_call_id=?",
                (run["id"], "sample-failure-call"),
            ).fetchone()
        assert call is not None and json.loads(call["result"])["status"] == "unknown_outcome"
    finally:
        await provider.close()
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


@pytest.mark.asyncio
async def test_failed_disconnect_retains_integration_and_vault_credentials(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(workspace_tmp_path / "mcp-shutdown-failure.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(
            name="Shutdown failure fixture",
            command=sys.executable,
            args=[str(FIXTURE)],
            secret_env=["API_TOKEN"],
        )
    )
    credentials = MemoryCredentials()
    credentials.set("fixture-secret", f"mcp:{server.id}:API_TOKEN")
    manager = MCPManager(store, credentials)  # type: ignore[arg-type]
    try:
        tools = await manager.connect_server(server.id)
        add = next(tool for tool in tools if tool.name == "add")
        registry.set_permission(server.id, add.name, "read", add.schema_sha256)
        connection = manager._connected[server.id]

        async def fail_close() -> None:
            raise OSError("fixture shutdown failure")

        monkeypatch.setattr(connection, "close", fail_close)
        assert not await manager.disconnect_server(server.id)
        assert manager.connection_present(server.id)
        assert manager.server_status(server.id) == (
            "error",
            "The MCP server did not shut down cleanly.",
        )
        assert manager.tools_for_run(set()) == []
        assert not await manager.delete_server(server.id)
        assert registry.get_server(server.id) is not None
        assert credentials.get(f"mcp:{server.id}:API_TOKEN") == "fixture-secret"

        await manager.close()
        assert manager.connection_present(server.id)
        assert manager.server_status(server.id)[0] == "error"
        assert manager.tools_for_run(set()) == []

        monkeypatch.undo()
        assert await manager.disconnect_server(server.id)
        assert not manager.connection_present(server.id)
        assert await manager.delete_server(server.id)
        assert registry.get_server(server.id) is None
        assert credentials.get(f"mcp:{server.id}:API_TOKEN") is None
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


def test_mcp_api_keeps_integration_and_credentials_when_shutdown_is_uncertain(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(workspace_tmp_path / "mcp-api-shutdown-failure.sqlite3")
    credentials = MemoryCredentials()

    async def fail_disconnect(_manager: MCPManager, _server_id: str) -> bool:
        return False

    monkeypatch.setattr(MCPManager, "disconnect_server", fail_disconnect)
    monkeypatch.setattr(MCPManager, "connection_present", lambda _manager, _server_id: True)
    app = create_app(
        LMStudioProvider(), SecretStr(TOKEN), store=store, credential_store=credentials
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        created = client.post(
            "/mcp/servers",
            headers=headers,
            json={"name": "Shutdown failure", "command": sys.executable, "secret_env": ["TOKEN"]},
        )
        assert created.status_code == 201
        assert created.json()["trust_environment"] is False
        server_id = created.json()["id"]
        credentials.set("preserve-me", f"mcp:{server_id}:TOKEN")
        created_http = client.post(
            "/mcp/servers",
            headers=headers,
            json={
                "name": "HTTP proxy setting",
                "transport": "streamable_http",
                "endpoint": "https://example.test/mcp",
                "trust_environment": True,
            },
        )
        assert created_http.status_code == 201
        assert created_http.json()["trust_environment"] is True

        disconnected = client.post(f"/mcp/servers/{server_id}/disconnect", headers=headers)
        assert disconnected.status_code == 503
        deleted = client.delete(f"/mcp/servers/{server_id}", headers=headers)
        assert deleted.status_code == 503
        assert MCPRegistry(store).get_server(server_id) is not None
        assert credentials.get(f"mcp:{server_id}:TOKEN") == "preserve-me"


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
        advertised = manager.tools_for_run({qualified})
        assert [item["function"]["name"] for item in advertised] == [qualified]
        description = advertised[0]["function"]["description"]
        assert tool.schema_sha256 in description
        assert "45 seconds" in description
        assert "no automatic retries" in description
        assert "256 KiB" in description
        assert "output schema" in description
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


@pytest.mark.asyncio
async def test_installed_agent_plugin_mcp_explicit_activation_lifecycle(
    workspace_tmp_path: Path,
):
    source = workspace_tmp_path / "package-source"
    source.mkdir()
    (source / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "fixture-mcp-package",
                "version": "1.0.0",
                "description": "Local MCP lifecycle fixture",
            }
        ),
        encoding="utf-8",
    )
    (source / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": {
                    "local-helper": {
                        "type": "stdio",
                        "command": Path(sys.executable).name,
                        "args": ["${PLUGIN_ROOT}/server.py"],
                        "cwd": "${PLUGIN_ROOT}",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    shutil.copyfile(FIXTURE, source / "server.py")
    archive_path = workspace_tmp_path / "fixture-plugin.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for package_file in source.iterdir():
            archive.write(package_file, package_file.name)

    store = RunStore(workspace_tmp_path / "plugin-mcp.sqlite3")
    app = create_app(
        LMStudioProvider(),
        SecretStr(TOKEN),
        store=store,
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        installed = client.post(
            "/extensions/packages/install",
            headers=headers,
            json={"archive_path": str(archive_path)},
        )
        assert installed.status_code == 201
        package_root = Path(installed.json()["package"]["path"])
        discovered = client.post(
            "/extensions/packages/discover",
            headers=headers,
            json={"root": str(package_root)},
        )
        assert discovered.status_code == 200
        assert discovered.json()["packages"][0]["servers"][0]["name"] == "local-helper"

        imported = client.post(
            "/extensions/packages/import-mcp-server",
            headers=headers,
            json={
                "root": str(package_root),
                "package_name": "fixture-mcp-package",
                "server_name": "local-helper",
            },
        )
        assert imported.status_code == 201
        server_id = imported.json()["id"]
        assert imported.json()["state"] == "disconnected"
        assert (
            client.post(
                "/extensions/packages/remove",
                headers=headers,
                json={"package_name": "fixture-mcp-package"},
            ).status_code
            == 409
        )

    manager = MCPManager(store, None)
    try:
        tools = await manager.connect_server(server_id)
        assert {tool.name for tool in tools} == {"add", "slow"}
        add = next(tool for tool in tools if tool.name == "add")
        assert add.permission == "none"
        manager.registry.set_permission(server_id, "add", "read", add.schema_sha256)
        result = await manager.call_tool(
            add.qualified_name,
            {"left": 20, "right": 22},
            require_read=True,
        )
        assert result["status"] == "ok"
        assert result["structured_content"] == {"result": 42}
        assert result["trust"] == "untrusted_mcp_output"
        assert await manager.disconnect_server(server_id)
    finally:
        await manager.close()

    with TestClient(app) as client:
        removed_server = client.delete(f"/mcp/servers/{server_id}", headers=headers)
        assert removed_server.status_code == 204
        removed_package = client.post(
            "/extensions/packages/remove",
            headers=headers,
            json={"package_name": "fixture-mcp-package"},
        )
        assert removed_package.status_code == 200
        assert not package_root.exists()
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


def test_mcp_legacy_protocol_mode_is_authenticated_and_persisted(workspace_tmp_path: Path):
    store = RunStore(workspace_tmp_path / "mcp-legacy-mode.sqlite3")
    with TestClient(create_app(LMStudioProvider(), SecretStr(TOKEN), store=store)) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        created = client.post(
            "/mcp/servers",
            headers=headers,
            json={
                "name": "Legacy sampling fixture",
                "command": sys.executable,
                "args": [str(SAMPLING_FIXTURE)],
                "protocol_mode": "legacy",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["protocol_mode"] == "legacy"
        servers = client.get("/mcp/servers", headers=headers)
        assert servers.status_code == 200
        assert servers.json()[0]["protocol_mode"] == "legacy"
        run = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Use an explicitly approved legacy sampling server",
                "approved_mcp_sampling_servers": [created.json()["id"]],
            },
        )
        assert run.status_code == 201, run.text
        assert client.get(f"/runs/{run.json()['id']}", headers=headers).json()[
            "approved_mcp_sampling_servers"
        ] == [created.json()["id"]]
        bad_mode = client.post(
            "/mcp/servers",
            headers=headers,
            json={
                "name": "Invalid mode fixture",
                "command": sys.executable,
                "args": [str(SAMPLING_FIXTURE)],
                "protocol_mode": "future",
            },
        )
        assert bad_mode.status_code == 422

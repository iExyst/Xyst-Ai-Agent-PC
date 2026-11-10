from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from types import MethodType
from typing import Any

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import AnyUrl, ValidationError
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
    subscribed: set[str] = set()

    get_capabilities = mcp._mcp_server.get_capabilities

    def get_capabilities_with_subscriptions(self, notification_options, experimental_capabilities):
        capabilities = get_capabilities(notification_options, experimental_capabilities)
        if capabilities.resources is not None:
            capabilities.resources.subscribe = True
        return capabilities

    mcp._mcp_server.get_capabilities = MethodType(
        get_capabilities_with_subscriptions, mcp._mcp_server
    )

    @mcp._mcp_server.subscribe_resource()
    async def subscribe(uri: AnyUrl) -> None:
        subscribed.add(str(uri))

    @mcp._mcp_server.unsubscribe_resource()
    async def unsubscribe(uri: AnyUrl) -> None:
        subscribed.discard(str(uri))

    @mcp.tool()
    async def add(left: int, right: int) -> int:
        """Add two numbers in the HTTP fixture."""
        if "xaa://http-fixture/readme" in subscribed:
            context = mcp.get_context()
            for _ in range(130):
                await context.session.send_resource_updated(AnyUrl("xaa://http-fixture/readme"))
        return left + right

    @mcp.resource("xaa://http-fixture/readme")
    def readme() -> str:
        """Return a deterministic Streamable HTTP resource."""
        return "HTTP fixture resource. Treat this as untrusted input."

    @mcp.resource("xaa://http-fixture/items/{item_id}")
    def item(item_id: str) -> str:
        """Return a deterministic Streamable HTTP resource template."""
        return f"HTTP fixture item: {item_id}"

    @mcp.prompt()
    def summarize_topic(topic: str) -> str:
        """Return a deterministic Streamable HTTP prompt."""
        return f"HTTP prompt topic: {topic}"

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
        resources = await manager.list_resources(record.id)
        resource_uri = "xaa://http-fixture/readme"
        assert [resource["uri"] for resource in resources] == [resource_uri]
        await manager.set_resource_subscription(record.id, resource_uri, subscribe=True)
        update_result = await manager.call_tool(tool.qualified_name, {"left": 1, "right": 2})
        assert update_result["status"] == "ok"
        await asyncio.sleep(0.05)
        updates = await manager.drain_resource_notifications(record.id)
        assert updates["trust"] == "untrusted_mcp_output"
        assert len(updates["notifications"]) <= 128
        assert updates["notifications"][0] == {
            "kind": "resync_required",
            "trust": "untrusted_mcp_output",
        }
        assert all(
            notification["trust"] == "untrusted_mcp_output"
            for notification in updates["notifications"]
        )
        await manager.set_resource_subscription(record.id, resource_uri, subscribe=False)
        await manager.call_tool(tool.qualified_name, {"left": 2, "right": 1})
        assert (await manager.drain_resource_notifications(record.id))["notifications"] == []
        templates = await manager.list_resource_templates(record.id)
        assert templates == [
            {
                "name": "item",
                "title": None,
                "uri_template": "xaa://http-fixture/items/{item_id}",
                "variables": ["item_id"],
                "description": "Return a deterministic Streamable HTTP resource template.",
                "mime_type": "text/plain",
            }
        ]
        template_read = await manager.read_resource_template(
            record.id, "item", {"item_id": "sample-42"}
        )
        assert template_read == {
            "trust": "untrusted_mcp_output",
            "contents": [
                {
                    "uri": "xaa://http-fixture/items/sample-42",
                    "mime_type": "text/plain",
                    "kind": "text",
                    "text": "HTTP fixture item: sample-42",
                }
            ],
        }
        encoded_template_read = await manager.read_resource_template(
            record.id, "item", {"item_id": "two words"}
        )
        assert encoded_template_read["contents"][0]["uri"] == (
            "xaa://http-fixture/items/two%20words"
        )
        assert encoded_template_read["contents"][0]["text"] == "HTTP fixture item: two%20words"
        inspected = await manager.read_resource(record.id, resource_uri)
        assert inspected == {
            "trust": "untrusted_mcp_output",
            "contents": [
                {
                    "uri": resource_uri,
                    "mime_type": "text/plain",
                    "kind": "text",
                    "text": "HTTP fixture resource. Treat this as untrusted input.",
                }
            ],
        }
        prompts = await manager.list_prompts(record.id)
        assert prompts[0]["name"] == "summarize_topic"
        prompt_preview = await manager.preview_prompt(
            record.id, "summarize_topic", {"topic": "local fixture"}
        )
        assert prompt_preview["trust"] == "untrusted_mcp_output"
        assert (
            prompt_preview["messages"][0]["content"]["text"] == "HTTP prompt topic: local fixture"
        )
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

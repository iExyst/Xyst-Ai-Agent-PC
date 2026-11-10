from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from pydantic import ValidationError
from xaa.runtime.mcp_manager import MCPConnectionError, MCPManager
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
    mcp = MCPServer("HTTP fixture")

    @mcp.tool()
    async def add(left: int, right: int, ctx: Context) -> int:
        """Add two numbers in the HTTP fixture."""
        for _ in range(130):
            await ctx.notify_resource_updated("xaa://http-fixture/readme")
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
        assert encoded_template_read["contents"][0]["text"] == "HTTP fixture item: two words"
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


@pytest.mark.asyncio
async def test_streamable_http_oauth_pkce_vault_and_reconnect(workspace_tmp_path: Path):
    mcp = MCPServer("OAuth fixture")

    @mcp.tool()
    async def add(left: int, right: int) -> int:
        """Add numbers after OAuth authorization."""
        return left + right

    mcp_app = mcp.streamable_http_app()
    host = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    host.bind(("127.0.0.1", 0))
    port = host.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    endpoint = f"{origin}/mcp"
    issued_challenge: list[str] = []
    token_requests: list[str] = []

    async def respond(
        send: Any,
        status: int,
        payload: dict[str, Any],
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        import json

        body = json.dumps(payload).encode()
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        response_headers.extend(headers or [])
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})

    async def fixture(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await mcp_app(scope, receive, send)
            return
        path = scope["path"]
        if path == "/mcp":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            if auth not in {
                "Bearer fixture-oauth-token",
                "Bearer fixture-refreshed-token",
            }:
                await respond(
                    send,
                    401,
                    {"error": "unauthorized"},
                    [
                        (
                            b"www-authenticate",
                            f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource/mcp"'.encode(),
                        )
                    ],
                )
                return
            await mcp_app(scope, receive, send)
            return
        if path == "/.well-known/oauth-protected-resource/mcp":
            await respond(
                send,
                200,
                {
                    "resource": endpoint,
                    "authorization_servers": [origin],
                    "scopes_supported": ["mcp"],
                },
            )
            return
        if path == "/.well-known/oauth-authorization-server":
            await respond(
                send,
                200,
                {
                    "issuer": origin,
                    "authorization_endpoint": f"{origin}/authorize",
                    "token_endpoint": f"{origin}/token",
                    "registration_endpoint": f"{origin}/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
            return
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        if path == "/register":
            import json

            registration = json.loads(body)
            await respond(send, 201, {**registration, "client_id": "fixture-client"})
        elif path == "/token":
            values = parse_qs(bytes(body).decode())
            grant_type = values.get("grant_type", [""])[0]
            token_requests.append(grant_type)
            if grant_type == "refresh_token":
                if values.get("refresh_token", [""])[0] not in {
                    "fixture-refresh",
                    "fixture-refresh-next",
                }:
                    await respond(send, 400, {"error": "invalid_grant"})
                else:
                    await respond(
                        send,
                        200,
                        {
                            "access_token": "fixture-refreshed-token",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                            "refresh_token": "fixture-refresh-next",
                        },
                    )
            else:
                verifier = values.get("code_verifier", [""])[0]
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                    .decode()
                    .rstrip("=")
                )
                if (
                    grant_type != "authorization_code"
                    or values.get("code", [""])[0] != "fixture-code"
                    or not issued_challenge
                    or challenge != issued_challenge[-1]
                ):
                    await respond(send, 400, {"error": "invalid_grant"})
                else:
                    await respond(
                        send,
                        200,
                        {
                            "access_token": "fixture-oauth-token",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                            "refresh_token": "fixture-refresh",
                        },
                    )
        else:
            await respond(send, 404, {"error": "not_found"})

    web_server = uvicorn.Server(
        uvicorn.Config(fixture, host="127.0.0.1", port=port, log_level="critical")
    )
    serving = asyncio.create_task(web_server.serve(sockets=[host]))
    store = RunStore(workspace_tmp_path / "mcp-oauth.sqlite3")
    credentials = MemoryCredentials()
    redirects: list[str] = []
    deny_authorization = False
    callback_messages: list[str] = []

    async def authorize(url: str) -> None:
        from httpx import AsyncClient

        redirects.append(url)
        params = parse_qs(urlsplit(url).query)
        issued_challenge.append(params["code_challenge"][0])
        callback_url = params["redirect_uri"][0]
        callback_params = {"state": params["state"][0]}
        if deny_authorization:
            callback_params["error"] = "access_denied"
        else:
            callback_params["code"] = "fixture-code"
        async with AsyncClient() as client:
            response = await client.get(callback_url + "?" + urlencode(callback_params))
            assert response.status_code == 200
            callback_messages.append(response.text)

    manager = MCPManager(store, credentials, authorization_redirect_handler=authorize)  # type: ignore[arg-type]
    try:
        async with asyncio.timeout(5):
            while not web_server.started:
                await asyncio.sleep(0.01)
        record = manager.registry.create_server(
            MCPServerSpec(name="OAuth fixture", transport="streamable_http", endpoint=endpoint)
        )
        tools = await manager.connect_server(record.id)
        assert [tool.name for tool in tools] == ["add"]
        assert len(redirects) == 1
        assert "code_challenge_method=S256" in redirects[0]
        assert token_requests == ["authorization_code"]
        assert credentials.get(f"mcp:{record.id}:oauth-tokens") is not None
        assert await manager.disconnect_server(record.id)
        saved_tokens = json.loads(credentials.get(f"mcp:{record.id}:oauth-tokens") or "{}")
        saved_tokens["expires_at"] = time.time() - 1
        credentials.set(json.dumps(saved_tokens), f"mcp:{record.id}:oauth-tokens")
        await manager.connect_server(record.id)
        assert len(redirects) == 1
        assert token_requests == ["authorization_code", "refresh_token"]
        refreshed = json.loads(credentials.get(f"mcp:{record.id}:oauth-tokens") or "{}")
        assert refreshed["token"]["access_token"] == "fixture-refreshed-token"
        assert await manager.disconnect_server(record.id)
        credentials.set(json.dumps(refreshed["token"]), f"mcp:{record.id}:oauth-tokens")
        await manager.connect_server(record.id)
        assert len(redirects) == 1
        assert token_requests == ["authorization_code", "refresh_token", "refresh_token"]
        assert await manager.delete_server(record.id)
        assert credentials.get(f"mcp:{record.id}:oauth-tokens") is None
        assert credentials.get(f"mcp:{record.id}:oauth-client") is None
        deny_authorization = True
        denied_record = manager.registry.create_server(
            MCPServerSpec(name="OAuth denied", transport="streamable_http", endpoint=endpoint)
        )
        with pytest.raises(MCPConnectionError, match="MCP sign-in could not be completed"):
            await manager.connect_server(denied_record.id)
        assert callback_messages[-1] == "Sign-in was not completed. You can close this window."
        assert credentials.get(f"mcp:{denied_record.id}:oauth-tokens") is None
        assert manager.server_status(denied_record.id)[0] != "connected"
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

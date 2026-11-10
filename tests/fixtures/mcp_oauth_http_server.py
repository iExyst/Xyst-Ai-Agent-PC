"""Deterministic local Streamable HTTP MCP OAuth fixture for native acceptance."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlencode

import uvicorn
from mcp.server.mcpserver import MCPServer


def create_app(port: int) -> Any:
    origin = f"http://127.0.0.1:{port}"
    endpoint = f"{origin}/mcp"
    server = MCPServer("Native OAuth fixture")
    challenges: dict[str, str] = {}

    @server.tool()
    def add(left: int, right: int) -> int:
        """Add integers after native desktop OAuth."""
        return left + right

    mcp_app = server.streamable_http_app()

    async def respond(
        send: Any, status: int, body: bytes, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-length", str(len(body)).encode()), *(headers or [])],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await mcp_app(scope, receive, send)
            return
        path = scope["path"]
        print(f"{scope['method']} {path}", flush=True)
        if path == "/health":
            await respond(send, 200, b"ok")
            return
        if path == "/mcp":
            headers = dict(scope["headers"])
            if headers.get(b"authorization") != b"Bearer native-oauth-token":
                await respond(
                    send,
                    401,
                    b'{"error":"unauthorized"}',
                    [
                        (
                            b"www-authenticate",
                            f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource/mcp"'.encode(),
                        ),
                        (b"content-type", b"application/json"),
                    ],
                )
                return
            await mcp_app(scope, receive, send)
            return
        if path == "/.well-known/oauth-protected-resource/mcp":
            body = json.dumps(
                {
                    "resource": endpoint,
                    "authorization_servers": [origin],
                    "scopes_supported": ["mcp"],
                }
            ).encode()
            await respond(send, 200, body, [(b"content-type", b"application/json")])
            return
        if path == "/.well-known/oauth-authorization-server":
            body = json.dumps(
                {
                    "issuer": origin,
                    "authorization_endpoint": f"{origin}/authorize",
                    "token_endpoint": f"{origin}/token",
                    "registration_endpoint": f"{origin}/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "code_challenge_methods_supported": ["S256"],
                }
            ).encode()
            await respond(send, 200, body, [(b"content-type", b"application/json")])
            return
        query = parse_qs(str(scope.get("query_string", b"").decode()))
        if path == "/authorize":
            print("authorization request received", flush=True)
            state = query.get("state", [""])[0]
            challenges[state] = query.get("code_challenge", [""])[0]
            callback = query.get("redirect_uri", [""])[0]
            if not state or not callback or not challenges[state]:
                await respond(send, 400, b"missing OAuth state, callback, or PKCE challenge")
                return
            location = f"{callback}?{urlencode({'state': state, 'code': 'native-fixture-code'})}"
            await respond(send, 302, b"redirecting", [(b"location", location.encode())])
            return
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        if path == "/register":
            registration = json.loads(body)
            await respond(
                send,
                201,
                json.dumps({**registration, "client_id": "native-fixture-client"}).encode(),
                [(b"content-type", b"application/json")],
            )
            return
        if path == "/token":
            fields = parse_qs(body.decode())
            verifier = fields.get("code_verifier", [""])[0]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            state = fields.get("state", [""])[0]
            # The SDK's token exchange carries the authorization code. Validate the PKCE
            # verifier against the one captured on the real browser authorization request.
            expected = next(iter(challenges.values()), "")
            print(
                "token exchange received: "
                f"grant={fields.get('grant_type', [''])[0]} "
                f"code_ok={fields.get('code', [''])[0] == 'native-fixture-code'} "
                f"pkce_ok={challenge == expected}",
                flush=True,
            )
            if (
                fields.get("grant_type", [""])[0] != "authorization_code"
                or fields.get("code", [""])[0] != "native-fixture-code"
                or (state and state not in challenges)
                or challenge != expected
            ):
                await respond(
                    send,
                    400,
                    b'{"error":"invalid_grant"}',
                    [(b"content-type", b"application/json")],
                )
                return
            await respond(
                send,
                200,
                b'{"access_token":"native-oauth-token","token_type":"Bearer","expires_in":3600}',
                [(b"content-type", b"application/json")],
            )
            return
        await respond(send, 404, b"not found")

    return app


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    await uvicorn.Server(
        uvicorn.Config(
            create_app(args.port), host="127.0.0.1", port=args.port, log_level="critical"
        )
    ).serve()


if __name__ == "__main__":
    asyncio.run(main())

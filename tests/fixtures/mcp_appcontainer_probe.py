from __future__ import annotations

import json
import socket
import sys

port = int(sys.argv[-1])


def respond(message: dict[str, object]) -> dict[str, object] | None:
    message_id = message.get("id")
    method = message.get("method")
    if method == "initialize":
        params = message.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": requested,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "XAA AppContainer probe", "version": "1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": message_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "tools": [
                    {
                        "name": "try_loopback",
                        "description": "Try connecting to the test listener.",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                    }
                ]
            },
        }
    if method == "tools/call":
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                result = "connected"
        except OSError as error:
            result = f"blocked:{type(error).__name__}"
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {"content": [{"type": "text", "text": result}]},
        }
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


for line in sys.stdin:
    request = json.loads(line)
    response = respond(request)
    if response is not None:
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()

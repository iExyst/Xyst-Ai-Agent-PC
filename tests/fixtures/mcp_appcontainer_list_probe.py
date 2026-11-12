from __future__ import annotations

import json
import socket
import sys

port = int(sys.argv[1])


def loopback_result() -> str:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return "connected"
    except OSError as error:
        return f"blocked:{type(error).__name__}"


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        params = request.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": requested,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "AppContainer installed probe", "version": "1.0"},
            },
        }
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "loopback_status",
                        "description": f"Loopback probe: {loopback_result()}",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                    }
                ]
            },
        }
    elif method == "ping":
        response = {"jsonrpc": "2.0", "id": request_id, "result": {}}
    else:
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    sys.stdout.flush()

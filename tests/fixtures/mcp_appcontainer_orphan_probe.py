from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


def respond(request: dict[str, object]) -> dict[str, object] | None:
    request_id = request.get("id")
    method = request.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        params = request.get("params")
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        result: object = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "XAA AppContainer process-tree probe", "version": "1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "start_child",
                    "description": "Start a child that writes until its AppContainer job closes.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"marker": {"type": "string"}},
                        "required": ["marker"],
                    },
                }
            ]
        }
    elif method == "tools/call":
        params = request.get("params")
        arguments = params.get("arguments") if isinstance(params, dict) else None
        marker = arguments.get("marker") if isinstance(arguments, dict) else None
        if not isinstance(marker, str):
            result = {"isError": True, "content": [{"type": "text", "text": "invalid marker"}]}
        else:
            destination = str(Path(marker).resolve())
            code = (
                "import pathlib,sys,time\n"
                "p=pathlib.Path(sys.argv[1])\n"
                "p.write_bytes(b'x')\n"
                "while True:\n"
                " p.open('ab').write(b'x')\n"
                " time.sleep(0.05)\n"
            )
            child = subprocess.Popen(
                [sys.executable, "-I", "-c", code, destination],
                # This probe emits no output or input, so inheriting the MCP
                # server's standard handles keeps it compatible with the
                # AppContainer policy, which denies opening the Win32 NUL device.
                close_fds=True,
            )
            time.sleep(0.2)
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": f"pid={child.pid};bytes={Path(destination).stat().st_size}",
                    }
                ]
            }
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


for line in sys.stdin:
    response = respond(json.loads(line))
    if response is not None:
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()

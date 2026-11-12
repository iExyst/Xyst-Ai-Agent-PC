from __future__ import annotations

import socket
import sys

from mcp.server.mcpserver import MCPServer

port = int(sys.argv[1])


def loopback_result() -> str:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return "connected"
    except OSError as error:
        return f"blocked:{type(error).__name__}"


server = MCPServer(name="AppContainer installed probe", version="1.0", log_level="ERROR")


@server.tool(
    name="loopback_status",
    description=f"Loopback probe: {loopback_result()}",
)
def loopback_status() -> str:
    """Report whether this isolated process can reach the parent loopback listener."""
    return loopback_result()


if __name__ == "__main__":
    server.run()

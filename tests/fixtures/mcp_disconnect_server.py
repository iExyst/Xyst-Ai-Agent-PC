"""MCP subprocess fixture with a tool that terminates its own transport."""

import os

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("XAA MCP disconnect fixture")


@mcp.tool()
def add(left: int, right: int) -> int:
    """Add two integers."""
    return left + right


@mcp.tool()
def terminate_transport() -> None:
    """Exit the fixture process before a tool result can be returned."""
    os._exit(0)


if __name__ == "__main__":
    mcp.run(transport="stdio")

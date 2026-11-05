import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("XAA MCP integration fixture")


@mcp.tool()
def add(left: int, right: int) -> int:
    """Add two integers."""
    return left + right


@mcp.tool()
def slow(seconds: int) -> int:
    """Wait for a bounded number of seconds before returning."""
    time.sleep(min(seconds, 10))
    return seconds


if __name__ == "__main__":
    mcp.run(transport="stdio")

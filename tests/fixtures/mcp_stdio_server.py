from mcp.server.fastmcp import FastMCP

mcp = FastMCP("XAA MCP integration fixture")


@mcp.tool()
def add(left: int, right: int) -> int:
    """Add two integers."""
    return left + right


if __name__ == "__main__":
    mcp.run(transport="stdio")

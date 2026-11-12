from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ListRootsRequest, ListRootsResult

mcp = MCPServer("XAA roots interoperability fixture")


@mcp.tool()
async def inspect_roots(ctx: Context) -> str:
    """Ask the client for the explicitly shared filesystem roots."""
    result = await ctx.session.send_request(ListRootsRequest(), ListRootsResult)
    return "\n".join(f"{root.name}={root.uri}" for root in result.roots)


if __name__ == "__main__":
    mcp.run(transport="stdio")

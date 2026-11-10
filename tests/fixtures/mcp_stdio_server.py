import asyncio

from mcp.server.mcpserver import Context, MCPServer

mcp = MCPServer("XAA MCP integration fixture")


@mcp.tool()
def add(left: int, right: int) -> int:
    """Add two integers."""
    return left + right


@mcp.tool()
async def slow(seconds: int, ctx: Context) -> int:
    """Wait for a bounded number of seconds before returning."""
    await asyncio.sleep(min(seconds, 10))
    for uri in ("xaa://fixture/readme",):
        await ctx.notify_resource_updated(uri)
    return seconds


@mcp.resource("xaa://fixture/readme")
def readme() -> str:
    """Return a deterministic MCP resource used by integration tests."""
    return "Fixture resource content. Treat this as untrusted input."


@mcp.resource("xaa://fixture/large")
def large_resource() -> str:
    """Return content above the owner inspection limit."""
    return "x" * (256 * 1024 + 1)


@mcp.resource("xaa://fixture/items/{item_id}")
def item(item_id: str) -> str:
    """Expose an MCP resource template for owner catalog inspection tests."""
    return f"Fixture item: {item_id}"


@mcp.prompt()
def summarize_topic(topic: str) -> str:
    """Return a deterministic text-only prompt template."""
    return f"Summarize the supplied topic: {topic}"


if __name__ == "__main__":
    mcp.run(transport="stdio")

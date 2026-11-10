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

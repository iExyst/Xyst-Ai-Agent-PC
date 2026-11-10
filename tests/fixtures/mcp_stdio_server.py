import asyncio
from types import MethodType

from mcp.server.fastmcp import FastMCP
from pydantic import AnyUrl

mcp = FastMCP("XAA MCP integration fixture")
_subscribed: set[str] = set()

_get_capabilities = mcp._mcp_server.get_capabilities


def _get_capabilities_with_subscriptions(self, notification_options, experimental_capabilities):
    capabilities = _get_capabilities(notification_options, experimental_capabilities)
    if capabilities.resources is not None:
        capabilities.resources.subscribe = True
    return capabilities


mcp._mcp_server.get_capabilities = MethodType(_get_capabilities_with_subscriptions, mcp._mcp_server)


@mcp._mcp_server.subscribe_resource()
async def subscribe(uri: AnyUrl) -> None:
    _subscribed.add(str(uri))


@mcp._mcp_server.unsubscribe_resource()
async def unsubscribe(uri: AnyUrl) -> None:
    _subscribed.discard(str(uri))


@mcp.tool()
def add(left: int, right: int) -> int:
    """Add two integers."""
    return left + right


@mcp.tool()
async def slow(seconds: int) -> int:
    """Wait for a bounded number of seconds before returning."""
    await asyncio.sleep(min(seconds, 10))
    context = mcp.get_context()
    for uri in tuple(_subscribed):
        await context.session.send_resource_updated(AnyUrl(uri))
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

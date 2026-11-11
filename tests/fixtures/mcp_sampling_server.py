from mcp.server.mcpserver import Context, MCPServer
from mcp.types import SamplingMessage, TextContent

mcp = MCPServer("XAA sampling fixture")


@mcp.tool()
async def sample_text(ctx: Context) -> str:
    """Request one text-only sample from the connected MCP client."""
    response = await ctx.session.create_message(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(type="text", text="Return the fixture sampling phrase."),
            )
        ],
        max_tokens=100,
        include_context="none",
    )
    if not isinstance(response.content, TextContent):
        raise TypeError("Sampling fixture expected a text response.")
    return response.content.text


if __name__ == "__main__":
    mcp.run(transport="stdio")

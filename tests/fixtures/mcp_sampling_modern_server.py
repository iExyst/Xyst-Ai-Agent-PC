from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer, Resolve, Sample
from mcp.types import CreateMessageResult, SamplingMessage, TextContent

mcp = MCPServer("XAA modern sampling fixture")


def request_fixture_sample() -> Sample:
    return Sample(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(type="text", text="Return the fixture sampling phrase."),
            )
        ],
        max_tokens=100,
        include_context="none",
    )


@mcp.tool()
async def sample_text(
    response: Annotated[CreateMessageResult, Resolve(request_fixture_sample)],
) -> str:
    """Request one text-only sample through modern MCP multi-round-trip input."""
    if not isinstance(response.content, TextContent):
        raise TypeError("Sampling fixture expected a text response.")
    return response.content.text


if __name__ == "__main__":
    mcp.run(transport="stdio")

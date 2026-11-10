from __future__ import annotations

import base64

import pytest
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)
from xaa.runtime.mcp_manager import MCPConnectionError, _prompt_content_payload


def test_prompt_preview_text_and_media_content_is_bounded_and_typed():
    image = base64.b64encode(b"png-fixture").decode("ascii")
    audio = base64.b64encode(b"wav-fixture").decode("ascii")

    assert _prompt_content_payload(TextContent(type="text", text="fixture")) == {
        "kind": "text",
        "text": "fixture",
    }
    assert _prompt_content_payload(
        ImageContent(type="image", data=image, mime_type="image/png")
    ) == {"kind": "image", "mime_type": "image/png", "base64": image}
    assert _prompt_content_payload(
        AudioContent(type="audio", data=audio, mime_type="audio/wav")
    ) == {"kind": "audio", "mime_type": "audio/wav", "base64": audio}


def test_prompt_preview_embedded_resources_and_links_stay_metadata_only():
    text_resource = EmbeddedResource(
        type="resource",
        resource=TextResourceContents(
            type="text", uri="file:///fixture/readme", mime_type="text/plain", text="fixture"
        ),
    )
    blob_data = base64.b64encode(b"blob-fixture").decode("ascii")
    blob_resource = EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            type="blob", uri="file:///fixture/image", mime_type="image/png", blob=blob_data
        ),
    )
    link = ResourceLink(
        type="resource_link",
        uri="file:///fixture/linked",
        name="linked",
        mime_type="text/plain",
    )

    assert _prompt_content_payload(text_resource) == {
        "kind": "embedded_text_resource",
        "uri": "file:///fixture/readme",
        "mime_type": "text/plain",
        "text": "fixture",
    }
    assert _prompt_content_payload(blob_resource) == {
        "kind": "embedded_blob_resource",
        "uri": "file:///fixture/image",
        "mime_type": "image/png",
        "base64": blob_data,
    }
    linked = _prompt_content_payload(link)
    assert linked["kind"] == "resource_link"
    assert linked["uri"] == "file:///fixture/linked"
    assert "text" not in linked


def test_prompt_preview_rejects_invalid_or_oversized_media():
    with pytest.raises(MCPConnectionError, match="invalid base64"):
        _prompt_content_payload(ImageContent(type="image", data="not-base64", mime_type="image/png"))
    with pytest.raises(MCPConnectionError, match="256 KiB"):
        _prompt_content_payload(TextContent(type="text", text="x" * (256 * 1024 + 1)))

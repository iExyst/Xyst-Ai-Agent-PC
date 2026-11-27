from __future__ import annotations

import httpx
import pytest
from xaa.channels.a2a_outbound import A2AOutboundClient, A2AOutboundError, validate_agent_card_url


@pytest.mark.parametrize(
    "value",
    [
        "http://agent.example.test/.well-known/agent-card.json",
        "https://user:password@agent.example.test/.well-known/agent-card.json",
        "https://agent.example.test/.well-known/agent-card.json?token=secret",
        "https://agent.example.test/.well-known/agent-card.json#fragment",
        "https://agent.example.test:bad/.well-known/agent-card.json",
        "https:///path",
    ],
)
def test_outbound_agent_card_url_requires_clean_https_origin(value: str) -> None:
    with pytest.raises(ValueError):
        validate_agent_card_url(value)


def test_outbound_agent_card_accepts_a_valid_origin_root() -> None:
    assert validate_agent_card_url("https://agent.example.test") == "https://agent.example.test"


@pytest.mark.asyncio
async def test_outbound_client_rejects_cross_origin_interface_before_sending() -> None:
    posted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "supportedInterfaces": [
                        {
                            "url": "https://other.example.test/a2a",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ]
                },
            )
        posted = True
        return httpx.Response(200, json={})

    client = A2AOutboundClient(transport=httpx.MockTransport(handler))
    with pytest.raises(A2AOutboundError, match="same-origin"):
        await client.send_task(
            agent_card_url="https://agent.example.test/.well-known/agent-card.json",
            bearer_token="z" * 48,
            text="hello",
            message_id="message-1",
        )
    assert posted is False


@pytest.mark.asyncio
async def test_outbound_client_does_not_follow_redirects_or_forward_credentials() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "supportedInterfaces": [
                        {
                            "url": "https://agent.example.test/a2a",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ]
                },
            )
        return httpx.Response(307, headers={"Location": "https://other.example.test/collect"})

    token = "z" * 48
    client = A2AOutboundClient(transport=httpx.MockTransport(handler))
    with pytest.raises(A2AOutboundError, match="redirected"):
        await client.send_task(
            agent_card_url="https://agent.example.test/.well-known/agent-card.json",
            bearer_token=token,
            text="hello",
            message_id="message-2",
        )
    assert [request.url.host for request in requests] == [
        "agent.example.test",
        "agent.example.test",
    ]
    assert all(request.headers.get("authorization") == f"Bearer {token}" for request in requests)
    assert requests[1].headers.get("a2a-version") == "1.0"


@pytest.mark.asyncio
async def test_outbound_client_bounds_decompressed_response_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b" " * 65_537)

    client = A2AOutboundClient(transport=httpx.MockTransport(handler))
    with pytest.raises(A2AOutboundError, match="size limit"):
        await client.send_task(
            agent_card_url="https://agent.example.test/.well-known/agent-card.json",
            bearer_token="z" * 48,
            text="hello",
            message_id="message-3",
        )

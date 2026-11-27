from __future__ import annotations

import base64
import json

import httpx
import pytest
from xaa.channels.a2a_outbound import (
    A2AOutboundClient,
    A2AOutboundError,
    A2AUnsupportedInputError,
    decode_audio_data_url,
    decode_document_data_url,
    validate_agent_card_url,
)


def _fixture_wav() -> bytes:
    import wave
    from io import BytesIO

    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * 160)
    return output.getvalue()


def test_decode_outbound_audio_requires_valid_bounded_pcm_wav() -> None:
    content = _fixture_wav()
    encoded = "data:audio/wav;base64," + base64.b64encode(content).decode("ascii")
    assert decode_audio_data_url(encoded, "voice.wav") == (content, "voice.wav")
    with pytest.raises(ValueError, match="valid PCM WAV"):
        decode_audio_data_url("data:audio/wav;base64," + base64.b64encode(b"bad").decode(), None)
    with pytest.raises(ValueError, match="plain filename"):
        decode_audio_data_url(encoded, "../voice.wav")


@pytest.mark.asyncio
async def test_outbound_client_sends_advertised_wav_bytes_and_filename() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "defaultInputModes": ["text/plain", "audio/wav"],
                    "supportedInterfaces": [
                        {
                            "url": "https://agent.example.test/a2a",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                },
            )
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body["id"], "result": {"id": "remote-audio-1"}},
        )

    content = _fixture_wav()
    client = A2AOutboundClient(transport=httpx.MockTransport(handler))
    await client.send_task(
        agent_card_url="https://agent.example.test/.well-known/agent-card.json",
        bearer_token="z" * 48,
        text="Listen to this clip.",
        message_id="message-audio-1",
        audio=(content, "voice.wav"),
    )
    assert calls[0]["params"]["message"]["parts"] == [
        {"text": "Listen to this clip.", "mediaType": "text/plain"},
        {
            "raw": base64.b64encode(content).decode("ascii"),
            "mediaType": "audio/wav",
            "filename": "voice.wav",
        },
    ]


@pytest.mark.asyncio
async def test_outbound_client_rejects_unadvertised_wav_before_post() -> None:
    posted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "defaultInputModes": ["text/plain"],
                    "supportedInterfaces": [
                        {
                            "url": "https://agent.example.test/a2a",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                },
            )
        posted = True
        return httpx.Response(200, json={})

    client = A2AOutboundClient(transport=httpx.MockTransport(handler))
    with pytest.raises(A2AUnsupportedInputError, match="WAV audio"):
        await client.send_task(
            agent_card_url="https://agent.example.test/.well-known/agent-card.json",
            bearer_token="z" * 48,
            text="Listen.",
            message_id="message-audio-unsupported",
            audio=(_fixture_wav(), None),
        )
    assert posted is False


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


def test_decode_document_data_url_is_bounded_and_rejects_paths() -> None:
    encoded = base64.b64encode(b"# Hello").decode("ascii")
    assert decode_document_data_url(f"data:text/markdown;base64,{encoded}", "hello.md") == (
        b"# Hello",
        "text/markdown",
        "hello.md",
    )
    with pytest.raises(ValueError, match="plain filename"):
        decode_document_data_url(f"data:text/plain;base64,{encoded}", "..\\secret.txt")
    with pytest.raises(ValueError, match="encoding"):
        decode_document_data_url("data:text/plain;base64,@@@", "text.txt")
    with pytest.raises(ValueError, match="supported"):
        decode_document_data_url(f"data:application/octet-stream;base64,{encoded}", "file.bin")


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
async def test_outbound_client_cancels_and_validates_remote_task_identity() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "supportedInterfaces": [
                        {
                            "url": "https://agent.example.test",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ]
                },
            )
        call = json.loads(request.content)
        assert call["method"] == "CancelTask"
        assert call["params"] == {"id": "remote-task-4"}
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": call["id"],
                "result": {"id": "remote-task-4", "status": {"state": "TASK_STATE_CANCELED"}},
            },
        )

    client = A2AOutboundClient(transport=httpx.MockTransport(handler))
    task = await client.cancel_task(
        agent_card_url="https://agent.example.test/.well-known/agent-card.json",
        bearer_token="z" * 48,
        task_id="remote-task-4",
    )
    assert task["status"]["state"] == "TASK_STATE_CANCELED"
    assert requests[1].headers["a2a-version"] == "1.0"


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


@pytest.mark.asyncio
async def test_outbound_client_sends_advertised_document_bytes_and_filename() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "defaultInputModes": ["text/plain", "text/markdown"],
                    "supportedInterfaces": [
                        {
                            "url": "https://agent.example.test/a2a",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                },
            )
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body["id"], "result": {"id": "remote-1"}},
        )

    content = b"# Notes\nUntrusted peer input."
    client = A2AOutboundClient(transport=httpx.MockTransport(handler))
    await client.send_task(
        agent_card_url="https://agent.example.test/.well-known/agent-card.json",
        bearer_token="z" * 48,
        text="Review this note.",
        message_id="message-doc-1",
        document=(content, "text/markdown", "notes.md"),
    )
    assert calls[0]["params"]["message"]["parts"] == [
        {"text": "Review this note.", "mediaType": "text/plain"},
        {
            "raw": base64.b64encode(content).decode("ascii"),
            "mediaType": "text/markdown",
            "filename": "notes.md",
        },
    ]


@pytest.mark.asyncio
async def test_outbound_client_rejects_unadvertised_document_before_post() -> None:
    posted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "defaultInputModes": ["text/plain"],
                    "supportedInterfaces": [
                        {
                            "url": "https://agent.example.test/a2a",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                },
            )
        posted = True
        return httpx.Response(200, json={})

    client = A2AOutboundClient(transport=httpx.MockTransport(handler))
    with pytest.raises(A2AUnsupportedInputError, match="document type"):
        await client.send_task(
            agent_card_url="https://agent.example.test/.well-known/agent-card.json",
            bearer_token="z" * 48,
            text="Review this note.",
            message_id="message-doc-2",
            document=(b"notes", "text/markdown", "notes.md"),
        )
    assert posted is False

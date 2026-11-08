from __future__ import annotations

import io
import wave

import httpx
import pytest
from xaa.runtime.local_transcription import (
    TranscriptionError,
    transcribe_wav,
    validate_transcription_url,
    validate_wav,
)


def pcm_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 160)
    return output.getvalue()


def test_transcription_url_requires_loopback_and_exact_route() -> None:
    assert validate_transcription_url("http://127.0.0.1:8000/v1/audio/transcriptions")
    assert validate_transcription_url("http://[::1]:8000/v1/audio/transcriptions")
    for endpoint in (
        "https://example.com/v1/audio/transcriptions",
        "http://localhost/v1/audio/transcriptions",
        "http://127.0.0.1/v1/audio/transcriptions?proxy=1",
        "http://127.0.0.1/v1/audio/transcriptions/",
        "http://user:pass@127.0.0.1/v1/audio/transcriptions",
    ):
        with pytest.raises(TranscriptionError):
            validate_transcription_url(endpoint)


def test_wav_is_validated_for_pcm_length_and_size() -> None:
    validate_wav(pcm_wav())
    with pytest.raises(TranscriptionError, match="PCM WAV"):
        validate_wav(b"RIFFxxxxWAVE")
    with pytest.raises(TranscriptionError, match="4 MiB"):
        validate_wav(b"x" * (4 * 1024 * 1024 + 1))


@pytest.mark.asyncio
async def test_transcription_posts_audio_and_returns_bounded_text() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        assert b'name="model"' in body and b"whisper-1" in body
        assert b'name="file"; filename="audio.wav"' in body
        assert pcm_wav() in body
        return httpx.Response(200, json={"text": "  turn on the lights  "})

    transport = httpx.MockTransport(respond)
    assert (
        await transcribe_wav(
            pcm_wav(), "http://127.0.0.1:8000/v1/audio/transcriptions", transport=transport
        )
        == "turn on the lights"
    )


@pytest.mark.asyncio
async def test_transcription_rejects_redirects_and_bad_responses() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com"})

    with pytest.raises(TranscriptionError, match="returned an error"):
        await transcribe_wav(
            pcm_wav(),
            "http://127.0.0.1:8000/v1/audio/transcriptions",
            transport=httpx.MockTransport(respond),
        )

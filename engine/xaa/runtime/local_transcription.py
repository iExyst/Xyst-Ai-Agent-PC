"""Bounded WAV transcription through an explicitly selected loopback service."""

from __future__ import annotations

import ipaddress
import json
import wave
from io import BytesIO
from typing import cast
from urllib.parse import urlsplit

import httpx

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 48_000
TRANSCRIPTION_PATH = "/v1/audio/transcriptions"


class TranscriptionError(ValueError):
    """The local speech endpoint could not safely transcribe the selected WAV."""


def validate_transcription_url(value: str) -> str:
    """Accept only an HTTP(S) loopback endpoint at the transcription route."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise TranscriptionError("Enter a valid local transcription endpoint URL.") from None
    if (
        parsed.scheme not in {"http", "https"}
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != TRANSCRIPTION_PATH
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise TranscriptionError(
            "Transcription endpoint must use the exact /v1/audio/transcriptions path "
            "on a loopback address, without credentials, query or fragment."
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise TranscriptionError(
            "Use a numeric loopback address such as 127.0.0.1 or ::1."
        ) from None
    if not address.is_loopback:
        raise TranscriptionError("Transcription endpoint must be on this computer.")
    return value


def validate_wav(audio: bytes) -> None:
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise TranscriptionError("WAV audio must be between 1 byte and 4 MiB.")
    try:
        with wave.open(BytesIO(audio), "rb") as wav:
            if wav.getnchannels() not in {1, 2} or wav.getframerate() not in range(8_000, 97_001):
                raise TranscriptionError("WAV must use 1–2 channels and an 8–96 kHz sample rate.")
            if wav.getsampwidth() not in {1, 2, 3, 4} or wav.getnframes() < 1:
                raise TranscriptionError("WAV audio must contain PCM samples.")
            if wav.getnframes() > wav.getframerate() * 600:
                raise TranscriptionError("WAV audio may not exceed 10 minutes.")
    except (wave.Error, EOFError):
        raise TranscriptionError("Audio must be a valid PCM WAV file.") from None


async def transcribe_wav(
    audio: bytes,
    endpoint: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    validate_wav(audio)
    url = validate_transcription_url(endpoint)
    try:
        async with (
            httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(90.0, connect=3.0),
                transport=transport,
            ) as client,
            client.stream(
                "POST",
                url,
                data={"model": "whisper-1", "response_format": "json"},
                files={"file": ("audio.wav", audio, "audio/wav")},
            ) as response,
        ):
            if response.status_code != 200:
                raise TranscriptionError("Local transcription service returned an error.")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_TRANSCRIPT_BYTES:
                    raise TranscriptionError("Transcription response exceeds its size limit.")
    except TranscriptionError:
        raise
    except httpx.HTTPError:
        raise TranscriptionError(
            "Could not reach the selected local transcription service."
        ) from None
    try:
        result = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TranscriptionError(
            "Local transcription service returned an invalid response."
        ) from None
    if not isinstance(result, dict):
        raise TranscriptionError("Local transcription service returned no usable transcript.")
    result = cast(dict[str, object], result)
    text_value = result.get("text")
    if not isinstance(text_value, str):
        raise TranscriptionError("Local transcription service returned no usable transcript.")
    text = text_value
    if not text.strip() or len(text) > MAX_TRANSCRIPT_BYTES:
        raise TranscriptionError("Local transcription service returned no usable transcript.")
    return text.strip()

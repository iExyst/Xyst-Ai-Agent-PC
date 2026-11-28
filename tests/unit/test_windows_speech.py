from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from xaa.runtime import windows_speech
from xaa.runtime.local_transcription import TranscriptionError


def wav_bytes() -> bytes:
    import io
    import wave

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 80)
    return output.getvalue()


def ogg_opus_bytes(seconds: int = 1) -> bytes:
    from io import BytesIO

    import numpy as np
    import soundfile as sf

    output = BytesIO()
    sf.write(
        output,
        np.zeros((48_000 * seconds, 1), dtype="int16"),
        48_000,
        format="OGG",
        subtype="OPUS",
    )
    return output.getvalue()


def test_decodes_bounded_telegram_ogg_opus_to_mono_pcm_wav() -> None:
    import wave
    from io import BytesIO

    decoded = windows_speech.decode_telegram_ogg_opus(ogg_opus_bytes())
    with wave.open(BytesIO(decoded), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 16_000
        assert audio.getnframes() == 16_000


@pytest.mark.parametrize("audio", [b"", b"not an ogg file"])
def test_rejects_invalid_telegram_ogg_opus(audio: bytes) -> None:
    with pytest.raises(TranscriptionError):
        windows_speech.decode_telegram_ogg_opus(audio)


def test_rejects_telegram_ogg_opus_above_size_limit() -> None:
    with pytest.raises(TranscriptionError, match="4 MiB"):
        windows_speech.decode_telegram_ogg_opus(b"x" * (4 * 1024 * 1024 + 1))


def test_rejects_telegram_ogg_opus_above_duration_limit() -> None:
    with pytest.raises(TranscriptionError, match="60 seconds"):
        windows_speech.decode_telegram_ogg_opus(ogg_opus_bytes(seconds=61))


@pytest.mark.asyncio
async def test_transcribes_with_installed_windows_recognizer_and_bounded_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def create_process(*args: object, **kwargs: object) -> SimpleNamespace:
        assert args[0].endswith("powershell.exe")
        assert "-EncodedCommand" in args
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert kwargs["env"]["PATH"].endswith("WindowsPowerShell\\v1.0")
        return SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(
                return_value=(
                    json.dumps({"text": "hello world", "confidence": 0.92}).encode("utf-8"),
                    b"",
                )
            ),
            kill=lambda: None,
            wait=AsyncMock(),
        )

    monkeypatch.setattr(windows_speech.os, "name", "nt")
    monkeypatch.setattr(
        windows_speech.os, "environ", {"SystemRoot": r"C:\Windows", "OPENAI_API_KEY": "secret"}
    )
    monkeypatch.setattr(windows_speech.Path, "is_file", lambda _self: True)
    spawn = AsyncMock(side_effect=create_process)
    monkeypatch.setattr(windows_speech.asyncio, "create_subprocess_exec", spawn)

    result = await windows_speech.transcribe_windows_wav(wav_bytes())

    assert result == "hello world"
    assert spawn.await_count == 1
    assert spawn.await_args.kwargs["stdin"] is windows_speech.asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_windows_recognizer_requires_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows_speech.os, "name", "posix")

    with pytest.raises(TranscriptionError, match="available only on Windows"):
        await windows_speech.transcribe_windows_wav(wav_bytes())


@pytest.mark.asyncio
async def test_windows_recognizer_rejects_empty_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    async def create_process(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(return_value=(b'{"text":"  "}', b"")),
            kill=lambda: None,
            wait=AsyncMock(),
        )

    monkeypatch.setattr(windows_speech.os, "name", "nt")
    monkeypatch.setattr(windows_speech.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(windows_speech.asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(TranscriptionError, match="no usable transcript"):
        await windows_speech.transcribe_windows_wav(wav_bytes())


@pytest.mark.asyncio
async def test_windows_recognizer_reports_no_detected_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def create_process(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(return_value=(b'{"error":"no_speech_recognized"}', b"")),
            kill=lambda: None,
            wait=AsyncMock(),
        )

    monkeypatch.setattr(windows_speech.os, "name", "nt")
    monkeypatch.setattr(windows_speech.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(windows_speech.asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(TranscriptionError, match="did not detect speech.*microphone input"):
        await windows_speech.transcribe_windows_wav(wav_bytes())


@pytest.mark.asyncio
async def test_windows_recognizer_rejects_low_confidence_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def create_process(*_args: object, **_kwargs: object) -> SimpleNamespace:
        response = json.dumps({"text": "MIFF Callahan", "confidence": 0.0538807958})
        return SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(return_value=(response.encode("utf-8"), b"")),
            kill=lambda: None,
            wait=AsyncMock(),
        )

    monkeypatch.setattr(windows_speech.os, "name", "nt")
    monkeypatch.setattr(windows_speech.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(windows_speech.asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(TranscriptionError, match="recognized this audio with low confidence"):
        await windows_speech.transcribe_windows_wav(wav_bytes())


@pytest.mark.asyncio
async def test_cancellation_stops_windows_speech_child(monkeypatch: pytest.MonkeyPatch) -> None:
    children: list[SimpleNamespace] = []
    never_complete = asyncio.Event()

    async def communicate(_audio: bytes) -> tuple[bytes, bytes]:
        await never_complete.wait()
        return b"", b""

    async def create_process(*_args: object, **_kwargs: object) -> SimpleNamespace:
        child = SimpleNamespace(
            returncode=None,
            communicate=communicate,
            kill=Mock(),
            wait=AsyncMock(),
        )
        children.append(child)
        return child

    monkeypatch.setattr(windows_speech.os, "name", "nt")
    monkeypatch.setattr(windows_speech.Path, "is_file", lambda _self: True)
    spawn = AsyncMock(side_effect=create_process)
    monkeypatch.setattr(windows_speech.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(windows_speech.transcribe_windows_wav(wav_bytes()))
    await asyncio.sleep(0)
    assert children

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    children[0].kill.assert_called_once()
    children[0].wait.assert_awaited_once()

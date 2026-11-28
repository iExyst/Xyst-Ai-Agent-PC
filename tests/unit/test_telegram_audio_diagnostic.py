from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace

import pytest
from xaa.runtime import telegram_audio_diagnostic
from xaa.runtime.windows_speech import TranscriptionError


def test_packaged_telegram_audio_diagnostic_emits_bounded_local_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    audio = b"synthetic-ogg"

    async def transcribe(value: bytes) -> str:
        assert value == audio
        return "Hello world"

    monkeypatch.setattr(telegram_audio_diagnostic, "transcribe_windows_ogg_opus", transcribe)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(audio)))

    telegram_audio_diagnostic.main()

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "passed": True,
        "transcript": "Hello world",
        "input_bytes": len(audio),
        "network_used": False,
        "model_inference_requested": False,
    }


@pytest.mark.parametrize("audio", [b"", b"x" * (telegram_audio_diagnostic.MAX_INPUT_BYTES + 1)])
def test_packaged_telegram_audio_diagnostic_rejects_empty_and_oversized_input(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    audio: bytes,
) -> None:
    async def unexpected(_audio: bytes) -> str:
        raise AssertionError("Invalid payloads must be rejected before transcription.")

    monkeypatch.setattr(telegram_audio_diagnostic, "transcribe_windows_ogg_opus", unexpected)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(audio)))

    with pytest.raises(SystemExit, match="2"):
        telegram_audio_diagnostic.main()

    assert json.loads(capsys.readouterr().out) == {"passed": False, "error": "input_size"}


def test_packaged_telegram_audio_diagnostic_reports_safe_transcription_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def transcribe(_audio: bytes) -> str:
        raise TranscriptionError("fixture rejected")

    monkeypatch.setattr(telegram_audio_diagnostic, "transcribe_windows_ogg_opus", transcribe)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"malformed")))

    with pytest.raises(SystemExit, match="1"):
        telegram_audio_diagnostic.main()

    assert json.loads(capsys.readouterr().out) == {
        "passed": False,
        "error": "fixture rejected",
    }

"""Bounded packaged-engine diagnostic for Windows Telegram Ogg/Opus audio."""

from __future__ import annotations

import asyncio
import json
import sys

from xaa.runtime.windows_speech import TranscriptionError, transcribe_windows_ogg_opus

MAX_INPUT_BYTES = 4 * 1024 * 1024


def main() -> None:
    """Transcribe one bounded Ogg/Opus payload from stdin without storing or sending it."""
    audio = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not audio or len(audio) > MAX_INPUT_BYTES:
        print(json.dumps({"passed": False, "error": "input_size"}))
        raise SystemExit(2)
    try:
        transcript = asyncio.run(transcribe_windows_ogg_opus(audio))
    except TranscriptionError as error:
        print(json.dumps({"passed": False, "error": str(error)}))
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "passed": True,
                "transcript": transcript,
                "input_bytes": len(audio),
                "network_used": False,
                "model_inference_requested": False,
            }
        )
    )

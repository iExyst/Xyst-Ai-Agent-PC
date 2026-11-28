"""Exercise Telegram WAV ingress through the installed Windows offline recognizer."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import uuid4

from xaa.channels.telegram import (
    MAX_TELEGRAM_DOCUMENT_BYTES,
    TelegramIdentity,
    TelegramService,
    TelegramStore,
)
from xaa.credentials import CredentialStore
from xaa.storage.runs import RunStore, utc_now

PAIR_CODE = "telegram-wav-acceptance-pair-code-2026"
IDENTITY = TelegramIdentity("telegram-wav-acceptance", "xaa_wav_acceptance_bot")


class _Credentials(CredentialStore):
    def get(self, profile: str = "lm-studio") -> str | None:
        return "fixture-token" if profile == "telegram-bot" else None

    def set(self, token: str, profile: str = "lm-studio") -> None:
        raise AssertionError("The acceptance must not write credentials.")

    def delete(self, profile: str = "lm-studio") -> None:
        raise AssertionError("The acceptance must not delete credentials.")


class _FixtureBot:
    def __init__(self, token: str, audio: bytes) -> None:
        if token != "fixture-token":
            raise AssertionError("Unexpected Telegram credential.")
        self.audio = audio
        self.get_file_calls: list[str] = []
        self.download_calls = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get_me(self) -> TelegramIdentity:
        return IDENTITY

    async def get_updates(self, _offset: int | None) -> list[dict[str, object]]:
        update: dict[str, object] = {
            "update_id": 2,
            "message": {
                "document": {
                    "file_id": "fixture-wav",
                    "file_name": "synthetic-hello.wav",
                    "mime_type": "audio/wav",
                    "file_size": len(self.audio),
                },
                "from": {"id": 123},
                "chat": {"id": 123, "type": "private"},
            },
        }
        return [update, update]

    async def get_file(self, file_id: str) -> str:
        self.get_file_calls.append(file_id)
        return "documents/synthetic-hello.wav"

    async def download_file(self, _path: str, *, max_bytes: int) -> bytes:
        if max_bytes != MAX_TELEGRAM_DOCUMENT_BYTES:
            raise AssertionError("Telegram WAV must use the document download bound.")
        self.download_calls += 1
        return self.audio

    async def send_message(self, _chat_id: str, _text: str) -> int:
        raise AssertionError("The acceptance must not send an external message.")


def _synthetic_wav(powershell: Path) -> bytes:
    program = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.ASCIIEncoding]::new()
Add-Type -AssemblyName System.Speech
$synthesizer = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$stream = [System.IO.MemoryStream]::new()
try {
    $synthesizer.SetOutputToWaveStream($stream)
    $synthesizer.Speak('Hello world.')
}
finally { $synthesizer.Dispose() }
[Console]::Out.Write([System.Convert]::ToBase64String($stream.ToArray()))
$stream.Dispose()
"""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
            "USERNAME",
            "USERDOMAIN",
        }
    }
    environment["PATH"] = str(powershell.parent)
    result = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", program],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=environment,
        timeout=60,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError("Windows could not create synthetic speech for Telegram acceptance.")
    try:
        return base64.b64decode(result.stdout, validate=True)
    except ValueError:
        raise RuntimeError("Windows returned invalid synthetic speech data.") from None


async def _accept(audio: bytes, root: Path) -> dict[str, object]:
    runs = RunStore(root / "runs.sqlite3")
    store = TelegramStore(runs)
    store.save_bot(IDENTITY)
    store.create_pairing(PAIR_CODE)
    pairing = store.process_update(
        IDENTITY,
        {
            "update_id": 1,
            "message": {
                "text": f"/pair {PAIR_CODE}",
                "from": {"id": 123},
                "chat": {"id": 123, "type": "private"},
            },
        },
        model="fixture/model",
    )
    if pairing["state"] != "paired":
        raise RuntimeError("The local Telegram fixture user did not pair.")
    with runs.connection() as connection:
        now = utc_now()
        connection.execute(
            "INSERT INTO provider_profiles VALUES ('lm-studio','lm-studio','http://127.0.0.1:1234','fixture/model',NULL,NULL,?,?)",
            (now, now),
        )

    fixture = _FixtureBot("fixture-token", audio)
    submitted: list[str] = []
    service = TelegramService(
        runs,
        _Credentials(),
        submitted.append,
        api_factory=lambda _token: fixture,  # type: ignore[arg-type]
    )
    await service.cycle()
    with runs.connection() as connection:
        update = connection.execute(
            "SELECT run_id,state FROM telegram_updates WHERE account_id=? AND update_id=2",
            (IDENTITY.account_id,),
        ).fetchone()
    if update is None or update["state"] != "queued" or not update["run_id"]:
        raise RuntimeError("Telegram WAV did not create a queued shared-coordinator run.")
    run_id = str(update["run_id"])
    attached = runs.document_attachment(run_id)
    transcript = runs.document_text(run_id)
    if attached != (audio, "audio/wav", "synthetic-hello.wav") or not transcript:
        raise RuntimeError(
            "Telegram WAV source and transcript artifacts were not retained exactly."
        )

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z]+", " ", value.lower()).strip()

    assert attached is not None
    report: dict[str, object] = {
        "passed": normalize(transcript) == "hello world",
        "telegram_pairing_verified": True,
        "run_created": True,
        "run_state": str(update["state"]),
        "audio_artifact_byte_exact": attached[0] == audio,
        "transcript": transcript,
        "normalized_exact_match": normalize(transcript) == "hello world",
        "duplicate_update_skipped_media": fixture.get_file_calls == ["fixture-wav"]
        and fixture.download_calls == 1,
        "submitted_run_count": len(submitted),
        "credential_store_written": False,
        "external_telegram_api_used": False,
        "external_message_sent": False,
        "model_inference_requested": False,
    }
    if not report["passed"] or not report["duplicate_update_skipped_media"]:
        raise RuntimeError("Telegram offline WAV acceptance did not match its checks.")
    return report


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Telegram WAV acceptance requires Windows offline speech recognition.")
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise SystemExit("Windows PowerShell is not available.")
    root = Path(".acceptance") / f"telegram-wav-{uuid4()}"
    root.mkdir(parents=True, exist_ok=False)
    audio = _synthetic_wav(powershell)
    (root / "synthetic-hello.wav").write_bytes(audio)
    report = asyncio.run(_accept(audio, root))
    report["synthetic_audio_bytes"] = len(audio)
    report["tested_at"] = datetime.now(UTC).isoformat()
    report_path = root / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report_path": str(report_path)}, indent=2))
    if not report["passed"]:
        raise SystemExit("Windows offline speech did not match the synthetic phrase.")


if __name__ == "__main__":
    main()

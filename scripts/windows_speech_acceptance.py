"""Run an offline Windows SAPI recognition check against synthetic speech."""

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
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.windows_speech import transcribe_windows_wav
from xaa.storage.runs import RunStore


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Windows offline speech acceptance requires Windows.")
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise SystemExit("Windows PowerShell is not available.")

    root = Path(".acceptance") / f"windows-speech-{uuid4()}"
    root.mkdir(parents=True, exist_ok=False)
    audio_path = root / "synthetic-hello.wav"
    tts_script = r"""
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
    child_environment = {
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
    child_environment["PATH"] = str(powershell.parent)
    result = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            tts_script,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=child_environment,
        timeout=20,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise SystemExit("Windows could not create the local synthetic speech fixture.")

    audio = base64.b64decode(result.stdout)
    audio_path.write_bytes(audio)
    transcript = asyncio.run(transcribe_windows_wav(audio))

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z]+", " ", value.lower()).strip()

    passed = normalize(transcript) == "hello world"
    token = "windows-speech-acceptance-token-long-enough"
    encoded_audio = "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii")
    store = RunStore(root / "runs.sqlite3")
    with TestClient(
        create_app(
            LMStudioProvider(),
            SecretStr(token),
            store=store,
        )
    ) as client:
        response = client.post(
            "/runs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "objective": "Transcribe this synthetic local speech fixture.",
                "audio": encoded_audio,
                "transcription_provider": "windows_offline",
            },
        )
        if response.status_code != 201:
            raise SystemExit(
                f"Windows offline transcription API acceptance failed: {response.status_code}."
            )
        run_id = response.json()["id"]
        artifacts = store.list_artifacts(run_id)
        transcript_metadata = next(
            (item for item in artifacts if item["media_type"] == "text/plain"), None
        )
        source_metadata = next(
            (item for item in artifacts if item["media_type"] == "audio/wav"), None
        )
        if transcript_metadata is None or source_metadata is None:
            raise SystemExit("The Windows offline speech run did not retain both voice artifacts.")
        transcript_record = store.get_artifact(transcript_metadata["id"])
        source_record = store.get_artifact(source_metadata["id"])
        if transcript_record is None or source_record is None:
            raise SystemExit("The Windows offline speech artifact records could not be reloaded.")
        transcript_path = store.path.parent / "artifacts" / transcript_record["storage_key"]
        api_transcript = transcript_path.read_text(encoding="utf-8")
        if api_transcript != transcript:
            raise SystemExit("The API transcript artifact differs from Windows recognizer output.")
        stored_audio = store.path.parent / "artifacts" / source_record["storage_key"]
        if stored_audio.read_bytes() != audio:
            raise SystemExit("The API audio artifact differs from the submitted WAV.")
    report = {
        "passed": passed,
        "api_run_created": True,
        "audio_artifact_persisted": True,
        "transcript_artifact_persisted": True,
        "run_id": run_id,
        "provider": "installed Windows System.Speech recognizer",
        "recognizer_language": "system selected",
        "synthetic_phrase": "Hello world",
        "transcript": transcript,
        "normalized_exact_match": passed,
        "wav_bytes": len(audio),
        "network_used": False,
        "model_inference_requested": False,
        "tested_at": datetime.now(UTC).isoformat(),
    }
    report_path = root / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report_path": str(report_path)}, indent=2))
    if not passed:
        raise SystemExit("Windows offline speech recognition did not match the synthetic phrase.")


if __name__ == "__main__":
    main()

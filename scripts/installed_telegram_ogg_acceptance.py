"""Exercise packaged Windows Ogg/Opus decoding and offline speech recognition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from telegram_wav_acceptance import synthetic_ogg_opus, synthetic_wav

MAX_AUDIO_BYTES = 4 * 1024 * 1024


def _limited_environment() -> dict[str, str]:
    allowed = {
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
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    system_root = environment.get("SystemRoot", r"C:\Windows")
    environment["PATH"] = str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0")
    return environment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Packaged Telegram Ogg acceptance requires Windows.")
    engine = arguments.engine.resolve(strict=True)
    if not engine.is_file():
        raise SystemExit("Packaged engine must be a file.")
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    wav = synthetic_wav(powershell)
    ogg = synthetic_ogg_opus(wav)
    if not 0 < len(ogg) <= MAX_AUDIO_BYTES:
        raise RuntimeError("Synthetic Ogg/Opus fixture exceeded Telegram's audio bound.")
    engine_hash = hashlib.sha256(engine.read_bytes()).hexdigest()
    started = monotonic()
    result = subprocess.run(
        [str(engine), "--telegram-audio-diagnostic"],
        input=ogg,
        capture_output=True,
        env=_limited_environment(),
        timeout=70,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0 or len(result.stdout) > 64 * 1024:
        details = (result.stderr or result.stdout)[-2048:].decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Installed engine Ogg/Opus diagnostic returned {result.returncode}: {details}"
        )
    try:
        engine_report = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError(
            "Installed engine returned invalid Ogg/Opus diagnostic output."
        ) from None
    transcript = engine_report.get("transcript")

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z]+", " ", value.lower()).strip()

    report = {
        "schema_version": 1,
        "acceptance": "installed Windows package Telegram Ogg/Opus decode and offline recognition",
        "passed": bool(
            engine_report.get("passed") is True
            and isinstance(transcript, str)
            and normalize(transcript) == "hello world"
            and engine_report.get("input_bytes") == len(ogg)
            and engine_report.get("network_used") is False
            and engine_report.get("model_inference_requested") is False
        ),
        "engine_sha256": engine_hash,
        "input_sha256": hashlib.sha256(ogg).hexdigest(),
        "input_bytes": len(ogg),
        "transcript": transcript if isinstance(transcript, str) else None,
        "temporary_audio_file_used": True,
        "audio_retained_after_completion": False,
        "appcontainer_isolation": False,
        "network_used": False,
        "model_inference_requested": False,
        "elapsed_seconds": round(monotonic() - started, 3),
        "tested_at": datetime.now(UTC).isoformat(),
    }
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(
            "Installed Windows package did not recognize synthetic Telegram Ogg/Opus speech."
        )


if __name__ == "__main__":
    main()

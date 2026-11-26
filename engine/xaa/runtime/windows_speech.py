"""On-device transcription through an installed Windows speech recognizer."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
from pathlib import Path
from typing import cast

from xaa.runtime.local_transcription import MAX_TRANSCRIPT_BYTES, TranscriptionError, validate_wav

_POWERSHELL_PROGRAM = r"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$audioBytes = [System.Convert]::FromBase64String([Console]::In.ReadToEnd())
$audioStream = [System.IO.MemoryStream]::new($audioBytes)
$recognizerInfo = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | Select-Object -First 1
if ($null -eq $recognizerInfo) { throw 'No Windows speech recognizer is installed.' }
$recognizer = [System.Speech.Recognition.SpeechRecognitionEngine]::new($recognizerInfo)
try {
    $recognizer.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
    $recognizer.SetInputToWaveStream($audioStream)
    $result = $recognizer.Recognize([System.TimeSpan]::FromSeconds(30))
    if ($null -eq $result -or [string]::IsNullOrWhiteSpace($result.Text)) { throw 'No speech was recognized.' }
    if ($result.Text.Length -gt 48000) { throw 'The recognized text exceeds the response limit.' }
    [Console]::Out.Write((@{text=$result.Text; culture=$recognizerInfo.Culture.Name} | ConvertTo-Json -Compress))
}
finally {
    $recognizer.Dispose()
    $audioStream.Dispose()
}
"""
_POWERSHELL_ENCODED = base64.b64encode(_POWERSHELL_PROGRAM.encode("utf-16le")).decode("ascii")
_MAX_RESPONSE_BYTES = 64 * 1024
_RECOGNITION_TIMEOUT_SECONDS = 40


async def transcribe_windows_wav(audio: bytes) -> str:
    """Transcribe PCM WAV locally using the first installed Windows recognizer."""
    validate_wav(audio)
    if os.name != "nt":
        raise TranscriptionError("Windows offline speech recognition is available only on Windows.")
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise TranscriptionError(
            "Windows PowerShell is unavailable. Select a compatible local speech service instead."
        )

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
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = await asyncio.create_subprocess_exec(
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            _POWERSHELL_ENCODED,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_environment,
            creationflags=creationflags,
        )
    except OSError:
        raise TranscriptionError(
            "Could not start Windows offline speech recognition. Select a compatible local speech service instead."
        ) from None

    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(base64.b64encode(audio)),
            timeout=_RECOGNITION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        raise TranscriptionError(
            "Windows offline speech recognition timed out. Try a shorter recording."
        ) from None
    except asyncio.CancelledError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        raise

    if process.returncode != 0 or len(stdout) > _MAX_RESPONSE_BYTES:
        raise TranscriptionError(
            "Windows could not transcribe this audio. Check that a compatible offline speech language is installed, or select a local speech service."
        )
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TranscriptionError(
            "Windows speech recognition returned an invalid response."
        ) from None
    if not isinstance(result, dict):
        raise TranscriptionError("Windows speech recognition returned no usable transcript.")
    result = cast(dict[str, object], result)
    text = result.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TRANSCRIPT_BYTES:
        raise TranscriptionError("Windows speech recognition returned no usable transcript.")
    return text.strip()

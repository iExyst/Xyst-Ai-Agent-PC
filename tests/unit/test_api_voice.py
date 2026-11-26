from __future__ import annotations

import base64
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.storage.runs import RunStore


class QuietProvider(LMStudioProvider):
    async def close(self) -> None:
        return None


def wav_data() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\0\0" * 160)
    return output.getvalue()


def test_audio_run_uses_loopback_service_and_keeps_source_and_transcript(
    workspace_tmp_path,
) -> None:
    source_audio = wav_data()
    request_received = threading.Event()

    class TranscriptionHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request_received.set()
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            valid = (
                self.path == "/v1/audio/transcriptions"
                and b'filename="audio.wav"' in body
                and source_audio in body
                and b"whisper-1" in body
            )
            response = json.dumps(
                {"text": "untrusted spoken request"} if valid else {"error": "invalid multipart"}
            ).encode("utf-8")
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    speech_server = ThreadingHTTPServer(("127.0.0.1", 0), TranscriptionHandler)
    speech_thread = threading.Thread(target=speech_server.serve_forever, daemon=True)
    speech_thread.start()
    store = RunStore(workspace_tmp_path / "voice.sqlite3")
    token = "voice-test-token-that-is-long-enough-for-validation"
    encoded = "data:audio/wav;base64," + base64.b64encode(source_audio).decode("ascii")
    truncated = "data:audio/wav;base64," + base64.b64encode(source_audio[:-8]).decode("ascii")
    endpoint = f"http://127.0.0.1:{speech_server.server_port}/v1/audio/transcriptions"
    try:
        with TestClient(create_app(QuietProvider(), SecretStr(token), store=store)) as client:
            rejected = client.post(
                "/runs",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "objective": "Summarize the recording",
                    "audio": truncated,
                    "transcription_url": endpoint,
                },
            )
            assert rejected.status_code == 502
            assert not request_received.is_set()

            response = client.post(
                "/runs",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "objective": "Summarize the recording",
                    "audio": encoded,
                    "transcription_url": endpoint,
                },
            )
            assert response.status_code == 201
            assert request_received.is_set()
            run_id = response.json()["id"]
            artifacts = store.list_artifacts(run_id)
            assert [item["media_type"] for item in artifacts] == ["audio/wav", "text/plain"]
            wav_artifact = client.get(
                f"/artifacts/{artifacts[0]['id']}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert wav_artifact.content == source_audio
            assert wav_artifact.headers["content-type"] == "audio/wav"
            transcript_metadata = store.get_artifact(artifacts[1]["id"])
            assert transcript_metadata is not None
            transcript_path = store.path.parent / "artifacts" / transcript_metadata["storage_key"]
            assert transcript_path.read_text(encoding="utf-8") == "untrusted spoken request"
    finally:
        speech_server.shutdown()
        speech_server.server_close()
        speech_thread.join(timeout=2)


def test_audio_run_uses_windows_offline_provider_without_service_url(
    workspace_tmp_path,
    monkeypatch,
) -> None:
    source_audio = wav_data()
    received_audio: list[bytes] = []

    async def transcribe(audio: bytes) -> str:
        received_audio.append(audio)
        return "Windows offline transcript fixture"

    from xaa.runtime import windows_speech

    monkeypatch.setattr(windows_speech, "transcribe_windows_wav", transcribe)
    store = RunStore(workspace_tmp_path / "windows-voice.sqlite3")
    token = "windows-voice-test-token-long-enough-for-validation"
    encoded = "data:audio/wav;base64," + base64.b64encode(source_audio).decode("ascii")
    with TestClient(create_app(QuietProvider(), SecretStr(token), store=store)) as client:
        response = client.post(
            "/runs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "objective": "Summarize the local recording",
                "audio": encoded,
                "transcription_provider": "windows_offline",
            },
        )
        assert response.status_code == 201
        assert received_audio == [source_audio]
        artifacts = store.list_artifacts(response.json()["id"])
        assert [item["media_type"] for item in artifacts] == ["audio/wav", "text/plain"]
        transcript = store.get_artifact(artifacts[1]["id"])
        assert transcript is not None
        path = store.path.parent / "artifacts" / transcript["storage_key"]
        assert path.read_text(encoding="utf-8") == "Windows offline transcript fixture"


def test_windows_offline_transcription_rejects_service_url(workspace_tmp_path) -> None:
    source_audio = wav_data()
    store = RunStore(workspace_tmp_path / "windows-voice-url.sqlite3")
    token = "windows-voice-url-test-token-long-enough"
    encoded = "data:audio/wav;base64," + base64.b64encode(source_audio).decode("ascii")
    with TestClient(create_app(QuietProvider(), SecretStr(token), store=store)) as client:
        response = client.post(
            "/runs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "objective": "Summarize the local recording",
                "audio": encoded,
                "transcription_provider": "windows_offline",
                "transcription_url": "http://127.0.0.1:8000/v1/audio/transcriptions",
            },
        )
    assert response.status_code == 409
    assert "only used with an OpenAI-compatible local service" in response.json()["detail"]

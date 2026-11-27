"""Bounded A2A JSON-RPC input helpers for the shared run coordinator."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_A2A_ATTACHMENT_BYTES = 4 * 1024 * 1024
MAX_A2A_BODY_BYTES = 6 * 1024 * 1024 + 32 * 1024
MAX_A2A_TEXT_CHARS = 10_000
DOCUMENT_MEDIA_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/markdown",
    "text/plain",
}
LOCAL_AUDIO_MEDIA_TYPES = {"audio/wav"}
IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_METHODS = {
    "SendMessage",
    "SendStreamingMessage",
    "GetTask",
    "CancelTask",
    "message/send",
    "message/stream",
    "tasks/get",
    "tasks/cancel",
}
SEND_METHODS = {"SendMessage", "message/send"}
STREAM_METHODS = {"SendStreamingMessage", "message/stream"}
GET_METHODS = {"GetTask", "tasks/get"}
CANCEL_METHODS = {"CancelTask", "tasks/cancel"}


class A2ARequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jsonrpc: Literal["2.0"]
    id: str | int
    method: str = Field(min_length=1, max_length=64)
    params: dict[str, Any]


class InvalidA2ARequest(ValueError):
    """An authenticated A2A request failed bounded protocol validation."""

    def __init__(self, message: str, *, code: int = -32602, request_id: str | int = "") -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id


def parse_request(body: bytes) -> A2ARequest:
    """Validate one bounded A2A v1 JSON-RPC request and its supported input shape."""
    if len(body) > MAX_A2A_BODY_BYTES:
        raise InvalidA2ARequest(f"A2A request exceeds {MAX_A2A_BODY_BYTES} bytes.", code=-32600)
    try:
        value: object = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidA2ARequest("A2A request is not valid JSON.", code=-32700) from None
    if not isinstance(value, dict):
        raise InvalidA2ARequest("A2A request must be a JSON object.", code=-32600)
    request_data = cast(dict[str, Any], value)
    request_id_value = request_data.get("id", "")
    if isinstance(request_id_value, bool) or not isinstance(request_id_value, (str, int)):
        raise InvalidA2ARequest("A2A request ID must be a string or integer.", code=-32600)
    try:
        request = A2ARequest.model_validate(request_data)
    except ValidationError as exc:
        raise InvalidA2ARequest(
            "A2A JSON-RPC envelope is invalid.", request_id=request_id_value
        ) from exc
    if request.method not in _METHODS:
        raise InvalidA2ARequest("A2A method is not supported.", code=-32601, request_id=request.id)
    if request.method in SEND_METHODS | STREAM_METHODS:
        _validate_message(request.params, request.id)
    else:
        if not _valid_task_id(request.params.get("id") or request.params.get("taskId")):
            raise InvalidA2ARequest("A2A task ID is missing or invalid.", request_id=request.id)
        if request.method in GET_METHODS:
            history_length = request.params.get("historyLength")
            if history_length is not None and (
                type(history_length) is not int or history_length < 0
            ):
                raise InvalidA2ARequest(
                    "A2A historyLength must be a non-negative integer.", request_id=request.id
                )
    return request


def _valid_task_id(value: object) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _validate_message(params: dict[str, Any], request_id: str | int) -> None:
    message_value = params.get("message")
    message = cast(dict[str, Any], message_value) if isinstance(message_value, dict) else None
    if not isinstance(message, dict) or message.get("role") != "ROLE_USER":
        raise InvalidA2ARequest("A2A message must have role ROLE_USER.", request_id=request_id)
    message_id = message.get("messageId")
    if not _valid_task_id(message_id):
        raise InvalidA2ARequest("A2A messageId is missing or invalid.", request_id=request_id)
    context_id = message.get("contextId")
    if context_id is not None and not _valid_task_id(context_id):
        raise InvalidA2ARequest("A2A contextId is invalid.", request_id=request_id)
    parts_value = message.get("parts")
    if not isinstance(parts_value, list):
        raise InvalidA2ARequest(
            "A2A message must contain 1–32 supported parts.", request_id=request_id
        )
    parts = cast(list[object], parts_value)
    if not 1 <= len(parts) <= 32:
        raise InvalidA2ARequest(
            "A2A message must contain 1–32 supported parts.", request_id=request_id
        )
    attachment_parts = 0
    for part_value in parts:
        if not isinstance(part_value, dict):
            raise InvalidA2ARequest("A2A message part is invalid.", request_id=request_id)
        part = cast(dict[str, Any], part_value)
        if "text" in part and (
            isinstance(part.get("text"), str)
            and part.get("mediaType") in (None, "text/plain")
            and not any(key in part for key in ("raw", "url", "data"))
        ):
            continue
        if "raw" in part or "url" in part:
            media_type = part.get("mediaType")
            encoded = part.get("raw")
            if (
                not isinstance(encoded, str)
                or not isinstance(media_type, str)
                or "url" in part
                or "text" in part
                or "data" in part
            ):
                raise InvalidA2ARequest(
                    "A2A attachments must contain inline raw bytes; remote file URLs are not fetched.",
                    request_id=request_id,
                )
            if media_type not in IMAGE_MEDIA_TYPES | DOCUMENT_MEDIA_TYPES | LOCAL_AUDIO_MEDIA_TYPES:
                raise InvalidA2ARequest(
                    "This A2A endpoint does not support the attachment media type.",
                    request_id=request_id,
                )
            attachment_limit = (
                3 * 1024 * 1024 if media_type in IMAGE_MEDIA_TYPES else MAX_A2A_ATTACHMENT_BYTES
            )
            attachment_parts += 1
            if attachment_parts > 1:
                raise InvalidA2ARequest(
                    "Only one A2A image, document or audio attachment is supported per task.",
                    request_id=request_id,
                )
            try:
                if len(encoded) > ((attachment_limit + 2) // 3) * 4:
                    raise ValueError
                raw = base64.b64decode(encoded, validate=True)
                if not raw or len(raw) > attachment_limit:
                    raise ValueError
            except (ValueError, binascii.Error):
                raise InvalidA2ARequest(
                    f"A2A attachment bytes are invalid or exceed {attachment_limit // (1024 * 1024)} MiB.",
                    request_id=request_id,
                ) from None
            if "filename" in part and (
                not isinstance(part["filename"], str) or len(part["filename"]) > 255
            ):
                raise InvalidA2ARequest(
                    "A2A attachment filename is invalid or too long.", request_id=request_id
                )
            continue
        raise InvalidA2ARequest(
            "This A2A endpoint accepts text/plain and one inline image, document or WAV audio part.",
            request_id=request_id,
        )
    text = extract_text(message)
    if not text or len(text) > MAX_A2A_TEXT_CHARS:
        raise InvalidA2ARequest(
            "A2A message text must contain 1–10,000 characters.", request_id=request_id
        )


def extract_image_data_url(message: Mapping[str, Any]) -> str | None:
    """Return the single inline raw image part as a data URL; never dereference peer URLs."""
    raw_parts = message.get("parts")
    if not isinstance(raw_parts, list):
        return None
    for value in cast(list[object], raw_parts):
        if not isinstance(value, dict):
            continue
        part = cast(dict[str, Any], value)
        media_type = part.get("mediaType")
        encoded = part.get("raw")
        if (
            isinstance(media_type, str)
            and media_type in IMAGE_MEDIA_TYPES
            and isinstance(encoded, str)
        ):
            return f"data:{media_type};base64,{encoded}"
    return None


def extract_document_attachment(
    message: Mapping[str, Any],
) -> tuple[bytes, str, str | None] | None:
    """Decode one bounded inline document or WAV; never dereference peer URLs."""
    raw_parts = message.get("parts")
    if not isinstance(raw_parts, list):
        return None
    for value in cast(list[object], raw_parts):
        if not isinstance(value, dict):
            continue
        part = cast(dict[str, Any], value)
        media_type = part.get("mediaType")
        encoded = part.get("raw")
        if not isinstance(media_type, str) or media_type not in (
            DOCUMENT_MEDIA_TYPES | LOCAL_AUDIO_MEDIA_TYPES
        ):
            continue
        if "raw" not in part:
            continue
        if not isinstance(encoded, str):
            raise TypeError("A2A file attachment must contain inline raw bytes.")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("A2A file attachment bytes are invalid.") from None
        if not content or len(content) > MAX_A2A_ATTACHMENT_BYTES:
            raise ValueError("A2A file attachment must be between 1 byte and 4 MiB.")
        filename = part.get("filename")
        return content, media_type, filename if isinstance(filename, str) else None
    return None


def extract_text(message: Mapping[str, Any]) -> str:
    """Join the bounded text parts without interpreting them as permissions."""
    raw_parts = message.get("parts")
    if not isinstance(raw_parts, list):
        return ""
    parts = cast(list[object], raw_parts)
    return "\n".join(
        cast(dict[str, Any], part)["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(cast(dict[str, Any], part).get("text"), str)
    ).strip()


def request_identity(peer_digest: str, message_id: str) -> str:
    """Return a stable, non-secret run idempotency identity for one authenticated peer message."""
    if re.fullmatch(r"[0-9a-f]{64}", peer_digest) is None or not _valid_task_id(message_id):
        raise ValueError("A2A request identity inputs are invalid.")
    return f"a2a:{peer_digest[:24]}:{hashlib.sha256(message_id.encode('utf-8')).hexdigest()}"


def request_digest(params: Mapping[str, Any]) -> str:
    """Hash the semantic request payload, excluding the JSON-RPC transport request ID."""
    encoded = json.dumps(params, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def jsonrpc_result(request_id: str | int, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id: str | int, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def task_state(run_state: str) -> str:
    """Map durable coordinator states to A2A v1 task states."""
    if run_state in {"completed"}:
        return "TASK_STATE_COMPLETED"
    if run_state in {"failed"}:
        return "TASK_STATE_FAILED"
    if run_state in {"cancelled"}:
        return "TASK_STATE_CANCELED"
    if run_state in {"blocked", "paused", "waiting_for_approval", "verifying"}:
        return "TASK_STATE_INPUT_REQUIRED"
    if run_state == "queued":
        return "TASK_STATE_SUBMITTED"
    return "TASK_STATE_WORKING"


def task_record(
    run: Mapping[str, Any],
    context_id: str,
    *,
    history: list[dict[str, object]] | None = None,
    artifacts: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    state = task_state(str(run["state"]))
    status: dict[str, object] = {
        "state": state,
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    if run["state"] == "verifying":
        task_id = str(run["id"])
        review_id = "xaa-review-" + hashlib.sha256(task_id.encode()).hexdigest()[:20]
        status["message"] = {
            "messageId": review_id,
            "contextId": context_id,
            "taskId": task_id,
            "role": "ROLE_AGENT",
            "parts": [
                {
                    "text": "The candidate response is awaiting owner review in X.A.A.",
                    "mediaType": "text/plain",
                }
            ],
        }
    task: dict[str, object] = {
        "id": str(run["id"]),
        "contextId": context_id,
        "status": status,
    }
    if history is not None:
        task["history"] = history
    if artifacts is not None:
        task["artifacts"] = artifacts
    return task


def stream_task(task: dict[str, object]) -> dict[str, object]:
    return {"task": task}


def status_update(task_id: str, context_id: str, state: str) -> dict[str, object]:
    return {
        "statusUpdate": {
            "taskId": task_id,
            "contextId": context_id,
            "status": {
                "state": state,
                "timestamp": datetime.now(UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
            },
        }
    }


def artifact_update(
    task_id: str,
    context_id: str,
    text: str,
    *,
    append: bool = False,
    last_chunk: bool = False,
) -> dict[str, object]:
    return {
        "artifactUpdate": {
            "taskId": task_id,
            "contextId": context_id,
            "artifact": {
                "artifactId": "xaa-" + hashlib.sha256(task_id.encode()).hexdigest()[:24],
                "parts": [{"text": text, "mediaType": "text/plain"}],
            },
            "append": append,
            "lastChunk": last_chunk,
        }
    }


def sse_data(payload: Mapping[str, object], request_id: str | int) -> str:
    return "data: " + json.dumps(jsonrpc_result(request_id, payload), ensure_ascii=False) + "\n\n"


def sse_done() -> str:
    return ": done\n\n"

"""Bounded, provenance-preserving run trajectory exports."""

from __future__ import annotations

import json
import re
from typing import Any, cast

from xaa.storage.runs import RunStore

MAX_TRAJECTORY_EVENTS = 10_000
MAX_TRAJECTORY_BYTES = 10 * 1024 * 1024
REDACTION_VERSION = 1
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_NAMED_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|secret|authorization)\b\s*[:=]\s*)"
    r"(['\"]?)[^\s,;\]\[}\"']+"
)
_TELEGRAM_TOKEN = re.compile(r"(?i)(/bot)[0-9]{5,}:[A-Za-z0-9_-]{20,}")


def redact(value: Any, secrets: tuple[str, ...]) -> Any:
    """Redact known credentials and common credential forms recursively."""
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if len(secret) >= 4:
                result = result.replace(secret, "[REDACTED]")
        result = _BEARER.sub("Bearer [REDACTED]", result)
        result = _NAMED_SECRET.sub(r"\1[REDACTED]", result)
        return _TELEGRAM_TOKEN.sub(r"\1[REDACTED]", result)
    if isinstance(value, list):
        items = cast(list[Any], value)
        return [redact(item, secrets) for item in items]
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return {key: redact(item, secrets) for key, item in mapping.items()}
    return value


def export_trajectory(
    store: RunStore, run_id: str, *, secrets: tuple[str, ...] = ()
) -> dict[str, Any] | None:
    """Build a bounded portable run record, event stream and artifact manifest."""
    run = store.get_run(run_id)
    if run is None:
        return None
    events = store.public_events(run_id)
    if len(events) > MAX_TRAJECTORY_EVENTS:
        raise ValueError("Run trajectory exceeds the event export limit.")
    trajectory = {
        "format": "xaa-trajectory",
        "format_version": 1,
        "redaction_version": REDACTION_VERSION,
        "run": {
            key: run[key]
            for key in (
                "id",
                "objective",
                "acceptance_criteria",
                "mode",
                "model",
                "state",
                "created_at",
                "updated_at",
            )
        },
        "events": events,
        "artifacts": store.list_artifacts(run_id),
    }
    sanitized = redact(trajectory, secrets)
    if len(json.dumps(sanitized, ensure_ascii=False).encode("utf-8")) > MAX_TRAJECTORY_BYTES:
        raise ValueError("Run trajectory exceeds the export size limit.")
    return sanitized

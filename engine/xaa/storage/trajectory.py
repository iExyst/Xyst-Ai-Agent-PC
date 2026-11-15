"""Bounded, provenance-preserving run trajectory exports."""

from __future__ import annotations

import json
from typing import Any

from xaa.redaction import redact
from xaa.storage.runs import RunStore

MAX_TRAJECTORY_EVENTS = 10_000
MAX_TRAJECTORY_BYTES = 10 * 1024 * 1024
REDACTION_VERSION = 3


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

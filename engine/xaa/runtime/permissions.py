"""Small policy helpers shared by workspace tools and task planning."""

from __future__ import annotations

from typing import Any


def workspace_write_authorized(run: dict[str, Any]) -> bool:
    """Workspace writes require Workspace mode or a saved automation grant."""
    return bool(run.get("workspace")) and run.get("mode") in {
        "workspace",
        "authorized_automation",
    }

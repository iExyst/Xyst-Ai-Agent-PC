"""Contracts shared by restricted command execution backends."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot

OutputCallback = Callable[[str, bytes], Awaitable[None]]


class CommandTransport(Protocol):
    """Run one immutable command intent and control its owned execution."""

    def require_intent(self, intent: CommandIntent) -> None: ...

    async def run(
        self,
        intent: CommandIntent,
        snapshot: WorkspaceSnapshot,
        *,
        deadline: float,
        on_output: OutputCallback | None = None,
    ) -> dict[str, Any]: ...

    async def control(
        self,
        action: Literal["stop", "cleanup", "status"],
        intent: CommandIntent,
        *,
        deadline: float,
    ) -> dict[str, Any]: ...

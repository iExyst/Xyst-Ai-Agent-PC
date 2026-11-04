"""Explicit run state machine."""

from enum import StrEnum


class RunState(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    VERIFYING = "verifying"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}

ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.QUEUED: frozenset({RunState.PLANNING, RunState.PAUSED, RunState.CANCELLED}),
    RunState.PLANNING: frozenset(
        {
            RunState.RUNNING,
            RunState.WAITING_FOR_APPROVAL,
            RunState.PAUSED,
            RunState.BLOCKED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.RUNNING: frozenset(
        {
            RunState.VERIFYING,
            RunState.WAITING_FOR_APPROVAL,
            RunState.PAUSED,
            RunState.BLOCKED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.VERIFYING: frozenset(
        {
            RunState.COMPLETED,
            RunState.RUNNING,
            RunState.WAITING_FOR_APPROVAL,
            RunState.PAUSED,
            RunState.BLOCKED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.WAITING_FOR_APPROVAL: frozenset(
        {RunState.RUNNING, RunState.PAUSED, RunState.CANCELLED}
    ),
    RunState.PAUSED: frozenset({RunState.QUEUED, RunState.CANCELLED}),
    RunState.BLOCKED: frozenset({RunState.QUEUED, RunState.CANCELLED}),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}

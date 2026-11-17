from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from xaa.runtime.states import RunState
from xaa.storage.runs import InvalidTransition, RunStore


def test_run_start_time_uses_first_planning_event_not_queue_creation(workspace_tmp_path) -> None:
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run = store.create_run(
        objective="Wait in queue before running",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture/model",
    )
    queued_at = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with store.connection() as connection:
        connection.execute("UPDATE runs SET created_at=? WHERE id=?", (queued_at, run["id"]))

    queued = store.get_run(run["id"])
    assert queued is not None
    assert queued["started_at"] is None

    store.transition(run["id"], RunState.PLANNING)
    planning_event = next(
        event
        for event in store.list_events(run["id"])
        if event["event_type"] == "run.state_changed"
        and event["payload"].get("to") == RunState.PLANNING.value
    )
    started = store.get_run(run["id"])
    assert started is not None
    assert started["started_at"] == planning_event["created_at"]
    assert started["started_at"] != started["created_at"]

    store.transition(run["id"], RunState.RUNNING)
    store.transition(run["id"], RunState.VERIFYING)
    continued = store.get_run(run["id"])
    assert continued is not None
    assert continued["started_at"] == planning_event["created_at"]


def test_run_state_and_events_survive_store_reopen(workspace_tmp_path) -> None:
    database = workspace_tmp_path / "runs.sqlite3"
    store = RunStore(database)
    run = store.create_run(
        objective="Summarize a fixture",
        acceptance_criteria=["Include the key result"],
        workspace=None,
        mode="observe",
        model="fixture/model",
    )
    run_id = run["id"]
    assert run["state"] == RunState.QUEUED

    store.transition(run_id, RunState.PLANNING)
    store.transition(run_id, RunState.RUNNING)
    store.transition(run_id, RunState.PAUSED)

    recovered = RunStore(database)
    assert recovered.get_run(run_id)["state"] == RunState.PAUSED
    recovered.transition(run_id, RunState.QUEUED)
    events = recovered.list_events(run_id)
    assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5]
    assert events[-1]["payload"]["to"] == RunState.QUEUED


def test_terminal_state_cannot_resume(workspace_tmp_path) -> None:
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run = store.create_run(
        objective="Task",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )
    store.transition(run["id"], RunState.CANCELLED)
    with pytest.raises(InvalidTransition):
        store.transition(run["id"], RunState.QUEUED)

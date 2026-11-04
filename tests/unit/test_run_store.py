from __future__ import annotations

import pytest
from xaa.runtime.states import RunState
from xaa.storage.runs import InvalidTransition, RunStore


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

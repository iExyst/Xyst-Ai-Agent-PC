import sqlite3

import pytest
from xaa.runtime.states import RunState
from xaa.storage.runs import LeaseLost, RunStore


def test_second_worker_cannot_steal_live_lease(workspace_tmp_path):
    database = workspace_tmp_path / "leases.sqlite3"
    first = RunStore(database)
    second = RunStore(database)
    run = first.create_run(
        objective="Task", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )
    run_id = run["id"]
    token = first.acquire_lease(run_id, "worker-one")
    assert token
    assert not second.acquire_lease(run_id, "worker-two")
    assert not second.release_lease(run_id, "worker-two")
    first.renew_lease(run_id, token)
    assert first.release_lease(run_id, token)
    assert second.acquire_lease(run_id, "worker-two")


def test_expired_worker_is_fenced_after_takeover(workspace_tmp_path):
    database = workspace_tmp_path / "leases.sqlite3"
    store = RunStore(database)
    run_id = store.create_run(
        objective="Task", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    old_token = store.acquire_lease(run_id, "same-worker-name")
    assert old_token
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE runs SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", run_id),
        )
    connection.close()
    new_token = RunStore(database).acquire_lease(run_id, "same-worker-name")
    assert new_token and new_token != old_token
    before = store.list_events(run_id)
    with pytest.raises(LeaseLost):
        store.append_worker_event(run_id, old_token, "tool.result", {"result": "stale"})
    with pytest.raises(LeaseLost):
        store.transition(run_id, RunState.PLANNING, owner=old_token)
    with pytest.raises(LeaseLost):
        store.renew_lease(run_id, old_token)
    assert not store.release_lease(run_id, old_token)
    assert store.list_events(run_id) == before
    store.transition(run_id, RunState.PLANNING, owner=new_token)

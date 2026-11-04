import sqlite3

from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore


def test_recovery_preserves_live_workers_and_blocks_uncertain_actions(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "recovery.sqlite3")
    ids = []
    for name in ["live", "expired", "uncertain"]:
        run_id = store.create_run(
            objective=name, acceptance_criteria=[], workspace=None, mode="observe", model="fixture"
        )["id"]
        token = store.acquire_lease(run_id, name)
        store.transition(run_id, RunState.PLANNING, owner=token)
        if name == "uncertain":
            store.record_tool_intent(
                run_id,
                token,
                call_id="write",
                tool="write",
                version="1",
                arguments={},
                idempotency_key="write",
            )
        ids.append(run_id)
    connection = sqlite3.connect(store.path)
    try:
        with connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id IN (?, ?)",
                tuple(ids[1:]),
            )
    finally:
        connection.close()
    assert store.recover_abandoned_runs() == [ids[1]]
    assert store.get_run(ids[0])["state"] == "planning"
    assert store.get_run(ids[2])["state"] == "blocked"
    count = len(store.list_events(ids[1]))
    store.recover_abandoned_runs()
    assert len(store.list_events(ids[1])) == count

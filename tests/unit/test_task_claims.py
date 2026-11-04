import pytest
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def test_claims_are_distinct_and_failed_dependency_is_blocked(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "claims.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    first = TaskStore(runs)
    first.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(id="a", objective="A"),
            TaskDefinition(id="b", objective="B"),
            TaskDefinition(id="c", objective="C", dependencies=["a"]),
        ],
    )
    second = TaskStore(RunStore(runs.path))
    a = first.claim_ready(run_id, parent)
    b = second.claim_ready(run_id, parent)
    assert {a["task"]["local_id"], b["task"]["local_id"]} == {"a", "b"}
    with pytest.raises(ValueError, match="candidate"):
        first.record_outcome(run_id, parent, "a", a["token"], state="completed", result={})
    with pytest.raises(PermissionError):
        first.record_outcome(run_id, parent, "a", b["token"], state="failed", result={})
    first.record_outcome(
        run_id, parent, "a", a["token"], state="failed", result={"error": "fixture failure"}
    )
    assert second.claim_ready(run_id, parent) is None
    assert (
        next(row for row in second.records(run_id) if row["local_id"] == "c")["status"] == "blocked"
    )
    second.renew_worker(run_id, parent, "b", b["token"])
    assert second.recover_expired(run_id, parent) == []
    with runs.connection() as connection:
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    with pytest.raises(PermissionError):
        second.renew_worker(run_id, parent, "b", b["token"])
    assert second.recover_expired(run_id, parent) == ["b"]
    with pytest.raises(PermissionError):
        second.record_outcome(run_id, parent, "b", b["token"], state="verifying", result={})
    assert second.claim_ready(run_id, parent) is None
    assert second.recover_expired(run_id, parent) == []

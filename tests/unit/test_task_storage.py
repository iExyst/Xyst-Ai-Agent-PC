import pytest
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def test_saved_plan_survives_restart_and_rechecks_parent_permissions(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "plan.sqlite3")
    run_id = runs.create_run(
        objective="Plan", acceptance_criteria=[], workspace=None, mode="observe", model="fixture"
    )["id"]
    token = runs.acquire_lease(run_id, "planner")
    tasks = TaskStore(runs)
    with pytest.raises(PermissionError):
        tasks.save_plan(
            run_id,
            token,
            [TaskDefinition(id="escalation", objective="Write", permissions=["workspace.write"])],
        )
    tasks.save_plan(
        run_id,
        token,
        [
            TaskDefinition(id="research", objective="Research"),
            TaskDefinition(
                id="review", objective="Review", dependencies=["research"], role="reviewer"
            ),
        ],
    )
    recovered = TaskStore(RunStore(runs.path))
    assert recovered.readiness(run_id) == (["research"], [])
    assert recovered.records(run_id)[1]["dependencies"] == ["research"]
    with pytest.raises(ValueError, match="replaced"):
        tasks.save_plan(run_id, token, [TaskDefinition(id="new", objective="Replace")])

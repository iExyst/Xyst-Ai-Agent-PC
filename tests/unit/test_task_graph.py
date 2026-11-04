import pytest
from xaa.runtime.task_graph import TaskDefinition, TaskGraph


def graph(tasks):
    return TaskGraph(
        tasks, parent_permissions={"workspace.read"}, iteration_budget=16, seconds_budget=1000
    )


def test_independent_tasks_ready_and_failed_dependencies_block_transitively():
    plan = graph(
        [
            TaskDefinition(id="a", objective="Research"),
            TaskDefinition(id="b", objective="Review"),
            TaskDefinition(id="c", objective="Integrate", dependencies=["a"]),
            TaskDefinition(id="d", objective="Validate", dependencies=["c"]),
        ]
    )
    assert plan.readiness({"a": "queued", "b": "queued", "c": "queued", "d": "queued"}) == (
        ["a", "b"],
        [],
    )
    assert plan.readiness({"a": "failed", "b": "completed", "c": "queued", "d": "queued"}) == (
        [],
        ["c", "d"],
    )


def test_cycles_permissions_and_shared_budget_are_rejected():
    with pytest.raises(ValueError, match="cycle"):
        graph(
            [
                TaskDefinition(id="a", objective="A", dependencies=["b"]),
                TaskDefinition(id="b", objective="B", dependencies=["a"]),
            ]
        )
    with pytest.raises(PermissionError):
        graph([TaskDefinition(id="a", objective="A", permissions=["workspace.write"])])
    with pytest.raises(ValueError, match="budget"):
        graph([TaskDefinition(id="a", objective="A", max_iterations=17)])

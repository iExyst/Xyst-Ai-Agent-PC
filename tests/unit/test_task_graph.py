import pytest
from xaa.runtime.task_graph import (
    MAX_TASK_DEPENDENCY_DEPTH,
    TaskDefinition,
    TaskGraph,
)


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


def test_task_dependency_chain_has_an_explicit_maximum_depth():
    tasks = [
        TaskDefinition(
            id=f"task-{index}",
            objective=f"Task {index}",
            dependencies=[f"task-{index - 1}"] if index else [],
            max_iterations=1,
            max_seconds=1,
        )
        for index in range(MAX_TASK_DEPENDENCY_DEPTH)
    ]
    assert graph(tasks).tasks

    too_deep = tasks + [
        TaskDefinition(
            id=f"task-{MAX_TASK_DEPENDENCY_DEPTH}",
            objective="One level beyond the limit",
            dependencies=[f"task-{MAX_TASK_DEPENDENCY_DEPTH - 1}"],
            max_iterations=1,
            max_seconds=1,
        )
    ]
    with pytest.raises(ValueError, match="dependency depth"):
        TaskGraph(
            too_deep,
            parent_permissions={"workspace.read"},
            iteration_budget=MAX_TASK_DEPENDENCY_DEPTH + 1,
            seconds_budget=MAX_TASK_DEPENDENCY_DEPTH + 1,
        )


def test_task_artifact_and_output_contracts_are_bounded_and_explicit():
    with pytest.raises(ValueError):
        TaskDefinition(id="a", objective="A", input_artifacts=["x", "x"])
    with pytest.raises(ValueError):
        TaskDefinition(id="a", objective="A", input_artifacts=["x"] * 9)
    with pytest.raises(ValueError):
        TaskDefinition(id="a", objective="A", expected_outputs=["x" * 501])

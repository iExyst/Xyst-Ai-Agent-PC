import pytest
from xaa.runtime.planning import ProposedTask, SavePlanArguments
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


def test_nested_tasks_run_before_parent_and_parent_waits_for_reviewed_children():
    plan = graph(
        [
            TaskDefinition(id="aggregate", objective="Combine child findings"),
            TaskDefinition(id="inspect", objective="Inspect source", parent_id="aggregate"),
            TaskDefinition(id="review", objective="Review source evidence", parent_id="aggregate"),
        ]
    )
    assert plan.readiness({"aggregate": "queued", "inspect": "queued", "review": "queued"}) == (
        ["inspect", "review"],
        [],
    )
    assert plan.readiness({"aggregate": "queued", "inspect": "completed", "review": "queued"}) == (
        ["review"],
        [],
    )
    assert plan.readiness(
        {"aggregate": "queued", "inspect": "completed", "review": "completed"}
    ) == (["aggregate"], [])
    assert plan.readiness({"aggregate": "queued", "inspect": "failed", "review": "queued"}) == (
        ["review"],
        ["aggregate"],
    )


def test_task_hierarchy_rejects_cycles_broader_permissions_and_budgets():
    with pytest.raises(ValueError, match="cycle"):
        graph(
            [
                TaskDefinition(id="a", objective="A", parent_id="b"),
                TaskDefinition(id="b", objective="B", parent_id="a"),
            ]
        )


def test_nested_delegation_depth_is_bounded():
    tasks = [
        TaskDefinition(
            id=f"level-{index}",
            objective=f"Level {index}",
            parent_id=f"level-{index - 1}" if index else None,
            max_iterations=1,
            max_seconds=1,
        )
        for index in range(MAX_TASK_DEPENDENCY_DEPTH)
    ]
    assert graph(tasks).tasks
    too_deep = tasks + [
        TaskDefinition(
            id=f"level-{MAX_TASK_DEPENDENCY_DEPTH}",
            objective="Beyond the maximum nesting depth",
            parent_id=f"level-{MAX_TASK_DEPENDENCY_DEPTH - 1}",
            max_iterations=1,
            max_seconds=1,
        )
    ]
    with pytest.raises(ValueError, match="delegation depth"):
        TaskGraph(
            too_deep,
            parent_permissions={"workspace.read"},
            iteration_budget=MAX_TASK_DEPENDENCY_DEPTH + 1,
            seconds_budget=MAX_TASK_DEPENDENCY_DEPTH + 1,
        )
    with pytest.raises(PermissionError, match="parent's permissions"):
        graph(
            [
                TaskDefinition(id="parent", objective="Parent"),
                TaskDefinition(
                    id="child",
                    objective="Child",
                    parent_id="parent",
                    permissions=["workspace.read", "workspace.write"],
                ),
            ]
        )
    with pytest.raises(ValueError, match="parent task"):
        graph(
            [
                TaskDefinition(id="parent", objective="Parent", max_iterations=2),
                TaskDefinition(id="child", objective="Child", parent_id="parent", max_iterations=3),
            ]
        )


def test_nested_token_allowances_inherit_and_fit_the_shared_budget():
    tasks = [
        TaskDefinition(id="parent", objective="Parent", max_total_tokens=500),
        TaskDefinition(id="child", objective="Child", parent_id="parent"),
    ]
    with pytest.raises(ValueError, match="token allowances"):
        TaskGraph(
            tasks,
            parent_permissions={"workspace.read"},
            iteration_budget=16,
            seconds_budget=1000,
            token_budget=900,
        )
    TaskGraph(
        tasks,
        parent_permissions={"workspace.read"},
        iteration_budget=16,
        seconds_budget=1000,
        token_budget=1000,
    )

    token_expansion = [
        TaskDefinition(
            id="parent",
            objective="Parent",
            max_total_tokens=300,
            max_output_tokens=64,
        ),
        TaskDefinition(id="middle", objective="Middle", parent_id="parent"),
        TaskDefinition(id="leaf", objective="Leaf", parent_id="middle", max_total_tokens=301),
    ]
    with pytest.raises(ValueError, match="inherited parent cap"):
        TaskGraph(
            token_expansion,
            parent_permissions={"workspace.read"},
            iteration_budget=16,
            seconds_budget=1000,
            token_budget=2000,
            output_limit=80,
        )

    output_expansion = [
        TaskDefinition(
            id="parent",
            objective="Parent",
            max_total_tokens=300,
            max_output_tokens=64,
        ),
        TaskDefinition(id="middle", objective="Middle", parent_id="parent"),
        TaskDefinition(id="leaf", objective="Leaf", parent_id="middle", max_output_tokens=65),
    ]
    with pytest.raises(ValueError, match="inherited parent cap"):
        TaskGraph(
            output_expansion,
            parent_permissions={"workspace.read"},
            iteration_budget=16,
            seconds_budget=1000,
            token_budget=2000,
            output_limit=80,
        )


def test_task_artifact_and_output_contracts_are_bounded_and_explicit():
    with pytest.raises(ValueError):
        TaskDefinition(id="a", objective="A", input_artifacts=["x", "x"])
    with pytest.raises(ValueError):
        TaskDefinition(id="a", objective="A", input_artifacts=["x"] * 9)
    with pytest.raises(ValueError):
        TaskDefinition(id="a", objective="A", expected_outputs=["x" * 501])


def test_task_acceptance_criteria_are_bounded_and_nonempty():
    with pytest.raises(ValueError, match="acceptance_criteria"):
        TaskDefinition(id="a", objective="A", acceptance_criteria=["criterion"] * 17)
    with pytest.raises(ValueError, match="acceptance criteria"):
        TaskDefinition(id="a", objective="A", acceptance_criteria=[" "])
    with pytest.raises(ValueError, match="acceptance_criteria"):
        TaskDefinition(id="a", objective="A", acceptance_criteria=["x" * 501])
    with pytest.raises(ValueError, match="dependencies"):
        TaskDefinition(id="a", objective="A", dependencies=["b"] * 64)


def test_proposed_task_schema_exposes_acceptance_criteria_bounds():
    schema = SavePlanArguments.model_json_schema()
    criteria = schema["$defs"]["ProposedTask"]["properties"]["acceptance_criteria"]
    assert criteria["minItems"] == 1
    assert criteria["maxItems"] == 16
    assert criteria["items"]["maxLength"] == 500
    assert schema["$defs"]["ProposedTask"]["properties"]["dependencies"]["maxItems"] == 63

    with pytest.raises(ValueError):
        ProposedTask(
            id="a", objective="A", acceptance_criteria=["x" * 501], expected_outputs=["done"]
        )

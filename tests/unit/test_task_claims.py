import json

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
    recovered = next(row for row in second.records(run_id) if row["local_id"] == "b")
    assert recovered["status"] == "blocked"
    assert recovered["blocked_reason"] == "worker_lost_reconciliation_required"
    with pytest.raises(PermissionError):
        second.record_outcome(run_id, parent, "b", b["token"], state="verifying", result={})
    assert second.claim_ready(run_id, parent) is None
    assert second.recover_expired(run_id, parent) == []


def test_lost_worker_journal_allows_only_safe_saved_result_resume(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="read", objective="Read evidence")])
    claim = tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.inference.started",
            {
                "task_id": "read",
                "inference_id": "inference-1",
            },
        )
        runs.record_event(
            connection,
            run_id,
            "tokens.reserved",
            {
                "task_id": "read",
                "inference_id": "inference-1",
                "reserved_tokens": 9,
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "inference-1",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"README.md"}',
                            },
                        },
                    ],
                },
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.tool.result",
            {
                "task_id": "read",
                "call_id": "call-1",
                "journal_id": "read-1",
                "result": {"text": "actual", "sha256": "abc"},
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    assert summary["safe_to_resume"] is True
    assert summary["saved_tool_results"] == 1
    assert summary["pending_model_tool_calls"] == []
    assert summary["reserved_tokens"] == 0
    tasks.resolve_lost_worker(run_id, parent, "read", "resume")
    recovered = tasks.claim_ready(run_id, parent)
    assert recovered is not None
    assert recovered["task"]["status"] == "running"
    assert recovered["task"]["local_id"] == "read"
    assert recovered["token"] != claim["token"]


def test_lost_worker_with_unanswered_token_reservation_is_not_safe_to_resume(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "reservation-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="read", objective="Read evidence")])
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "read", "inference_id": "inference-pending"},
        )
        runs.record_event(
            connection,
            run_id,
            "tokens.reserved",
            {
                "task_id": "read",
                "inference_id": "inference-pending",
                "reserved_tokens": 11,
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "prior-empty-message",
                "message": {"role": "assistant", "content": ""},
                "usage": None,
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    assert summary["reserved_tokens"] == 11
    assert summary["safe_to_resume"] is False
    with pytest.raises(ValueError, match="Review the saved candidate"):
        tasks.resolve_lost_worker(run_id, parent, "read", "resume")


def test_lost_worker_cannot_resume_when_shared_run_budget_is_exhausted(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "exhausted-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=1,
        max_total_tokens=1,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="read", objective="Read evidence", max_iterations=1)],
    )
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "read", "inference_id": "inference-complete"},
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "inference-complete",
                "message": {"role": "assistant", "content": ""},
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    assert summary["safe_to_resume"] is False
    with pytest.raises(ValueError, match="Review the saved candidate"):
        tasks.resolve_lost_worker(run_id, parent, "read", "resume")


@pytest.mark.parametrize("exhausted_limit", ["iterations", "tokens"])
def test_lost_worker_cannot_resume_when_specialist_budget_is_exhausted(
    workspace_tmp_path, exhausted_limit
):
    runs = RunStore(workspace_tmp_path / f"task-budget-{exhausted_limit}.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=8,
        max_total_tokens=8,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    budgets = {"max_iterations": 1 if exhausted_limit == "iterations" else 4}
    if exhausted_limit == "tokens":
        budgets["max_total_tokens"] = 1
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="read", objective="Read evidence", **budgets)],
    )
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "read", "inference_id": "task-budget-inference"},
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "task-budget-inference",
                "message": {"role": "assistant", "content": ""},
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    assert tasks.recovery_summary(run_id, "read")["safe_to_resume"] is False


def test_lost_worker_pending_tool_call_cannot_be_resumed(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "uncertain-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="read", objective="Read evidence")])
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "earlier",
                "message": {"role": "assistant", "content": "An earlier answer."},
                "usage": None,
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "inference-1",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"README.md"}',
                            },
                        },
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"README.md"}',
                            },
                        },
                    ],
                },
                "usage": None,
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.tool.result",
            {
                "task_id": "read",
                "call_id": "call-1",
                "journal_id": "read-1",
                "result": {"text": "done"},
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    assert summary["safe_to_resume"] is False
    assert summary["candidate_reviewable"] is False
    assert summary["pending_model_tool_calls"] == [
        {"call_id": "call-1", "name": "workspace_read_file"}
    ]
    with pytest.raises(ValueError, match="Review the saved candidate"):
        tasks.resolve_lost_worker(run_id, parent, "read", "resume")


def test_lost_worker_final_candidate_enters_existing_review_state(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "candidate-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=["Report evidence"],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="read", objective="Read evidence", acceptance_criteria=["Report evidence"]
            )
        ],
    )
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "inference-1",
                "message": {"role": "assistant", "content": "The file says hello."},
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    assert tasks.recovery_summary(run_id, "read")["candidate_reviewable"] is True
    tasks.resolve_lost_worker(run_id, parent, "read", "review_candidate")
    recovered = next(row for row in tasks.records(run_id) if row["local_id"] == "read")
    assert recovered["status"] == "verifying"
    assert json.loads(recovered["result"]) == {"candidate": "The file says hello."}

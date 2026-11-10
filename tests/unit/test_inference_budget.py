import pytest
from xaa.runtime.planning import save_model_plan
from xaa.storage.runs import IterationBudgetExceeded, RunStore
from xaa.storage.tasks import TaskStore


def create(store, limit):
    run_id = store.create_run(
        objective="Budget",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=limit,
    )["id"]
    return run_id, store.acquire_lease(run_id, "parent")


def test_interrupted_parent_attempt_is_durable_and_response_does_not_double_charge(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "attempts.sqlite3")
    run_id, token = create(store, 1)
    attempt = store.reserve_parent_inference(run_id, token, limit=100)
    reopened = RunStore(store.path)
    assert reopened.inference_attempts(run_id) == 1
    assert reopened.token_usage(run_id)["unmeasured_calls"] == 1
    with pytest.raises(IterationBudgetExceeded):
        reopened.reserve_parent_inference(run_id, token, limit=100)
    reopened.append_worker_event(
        run_id,
        token,
        "model.message",
        {
            "inference_id": attempt,
            "message": {"role": "assistant", "content": "Candidate"},
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        },
    )
    assert reopened.inference_attempts(run_id) == 1
    assert reopened.token_usage(run_id) == {
        "prompt_tokens": 2,
        "completion_tokens": 1,
        "total_tokens": 3,
        "unmeasured_calls": 0,
    }


def test_legacy_responses_and_task_reservations_share_the_same_allowance(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "legacy.sqlite3")
    run_id, token = create(store, 3)
    store.append_worker_event(
        run_id, token, "model.message", {"message": {"role": "assistant", "content": "Legacy"}}
    )
    store.append_worker_event(run_id, token, "task.inference.started", {"task_id": "child"})
    assert store.inference_attempts(run_id) == 2
    store.reserve_parent_inference(run_id, token, limit=3)
    assert store.inference_attempts(run_id) == 3
    assert store.token_usage(run_id)["unmeasured_calls"] == 3


def test_plan_reservations_deduct_parent_attempts_and_keep_response_headroom(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "plan.sqlite3")
    run_id, token = create(store, 4)
    for _ in range(2):
        store.reserve_parent_inference(run_id, token, limit=4)
    task = {
        "id": "child",
        "objective": "Inspect",
        "acceptance_criteria": ["Evidence"],
        "expected_outputs": ["Evidence report"],
        "dependencies": [],
        "max_iterations": 2,
    }
    with pytest.raises(ValueError, match="remaining shared budget"):
        save_model_plan(store, run_id, token, "invalid", {"tasks": [task]})
    assert TaskStore(store).records(run_id) == []
    assert not store.unresolved_tool_calls(run_id)
    result = save_model_plan(
        store, run_id, token, "valid", {"tasks": [{**task, "max_iterations": 1}]}
    )
    assert result["ready"] == ["child"]

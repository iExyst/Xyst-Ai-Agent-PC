from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def test_plan_time_reservations_use_remaining_run_budget_without_invalidating_saved_plan(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "remaining-plan-time.sqlite3")
    identity = store.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_seconds=100,
    )["id"]
    parent = store.acquire_lease(identity, "parent")
    with store.connection() as connection:
        connection.execute(
            "UPDATE runs SET created_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=30)).isoformat(), identity),
        )
    tasks = TaskStore(store)
    with pytest.raises(ValueError, match="budget"):
        tasks.save_plan(
            identity,
            parent,
            [TaskDefinition(id="too-long", objective="Use stale full budget", max_seconds=80)],
        )

    fresh_identity = store.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_seconds=100,
    )["id"]
    fresh_parent = store.acquire_lease(fresh_identity, "parent")
    fresh_tasks = TaskStore(store)
    fresh_tasks.save_plan(
        fresh_identity,
        fresh_parent,
        [TaskDefinition(id="saved", objective="Keep saved plan readable", max_seconds=90)],
    )
    with store.connection() as connection:
        connection.execute(
            "UPDATE runs SET created_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=110)).isoformat(), fresh_identity),
        )
    assert fresh_tasks.readiness(fresh_identity) == (["saved"], [])


def test_plan_persistence_rechecks_time_budget_at_transaction_boundary(
    workspace_tmp_path, monkeypatch
):
    store = RunStore(workspace_tmp_path / "plan-time-transaction.sqlite3")
    identity = store.create_run(
        objective="Plan", acceptance_criteria=[], workspace=None, mode="observe", model="fixture"
    )["id"]
    parent = store.acquire_lease(identity, "parent")
    tasks = TaskStore(store)
    remaining = iter((100, 1))
    monkeypatch.setattr(tasks, "remaining_run_seconds", lambda _: next(remaining))

    with pytest.raises(ValueError, match="remaining shared time budget"):
        tasks.save_plan(
            identity,
            parent,
            [TaskDefinition(id="too-late", objective="Plan duration changed", max_seconds=10)],
        )

    assert tasks.records(identity) == []


def test_plan_api_returns_typed_tasks_without_worker_authority(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "api.sqlite3")
    run_id = store.create_run(
        objective="Plan", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        token,
        [TaskDefinition(id="inspect", objective="Inspect", max_total_tokens=500)],
    )
    claim = tasks.claim_ready(run_id, token)
    with store.connection() as connection:
        store.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "inspect", "inference_id": "attempt-1"},
        )
        store.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "inspect",
                "inference_id": "attempt-1",
                "usage": {"prompt_tokens": 60, "completion_tokens": 24, "total_tokens": 84},
            },
        )
    api_token = "a" * 32
    client = TestClient(create_app(LMStudioProvider(), SecretStr(api_token), store=store))
    path = f"/runs/{run_id}/plan"
    assert client.get(path).status_code == 401
    response = client.get(path, headers={"Authorization": f"Bearer {api_token}"})
    assert response.status_code == 200
    task = response.json()["tasks"][0]
    assert task["local_id"] == "inspect"
    assert task["status"] == "running"
    assert task["budgets"] == {
        "max_iterations": 4,
        "max_seconds": 120,
        "max_total_tokens": 500,
        "max_output_tokens": None,
    }
    assert task["resource_usage"]["inference_attempts"] == 1
    assert task["resource_usage"]["iterations_remaining"] == 3
    assert task["resource_usage"]["measured_tokens"] == 84
    assert task["resource_usage"]["tokens_remaining"] == 416
    assert task["resource_usage"]["usage_measured"] is True
    assert 0 <= task["resource_usage"]["seconds_remaining"] <= 120
    with store.connection() as connection:
        store.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "inspect", "inference_id": "attempt-2"},
        )
    updated = client.get(path, headers={"Authorization": f"Bearer {api_token}"}).json()
    usage = updated["tasks"][0]["resource_usage"]
    assert usage["inference_attempts"] == 2
    assert usage["iterations_remaining"] == 2
    assert usage["tokens_remaining"] is None
    assert usage["usage_measured"] is False
    assert claim["token"] not in response.text
    assert token not in response.text
    assert "checkpoint" not in task
    assert "result" not in task


def test_owner_can_cancel_only_a_queued_task_and_dependents_become_blocked(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "cancel-queued-api.sqlite3")
    run_id = store.create_run(
        objective="Plan", acceptance_criteria=[], workspace=None, mode="observe", model="fixture"
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(id="cancel-me", objective="Research", max_iterations=1, max_seconds=30),
            TaskDefinition(
                id="dependent",
                objective="Review",
                dependencies=["cancel-me"],
                max_iterations=1,
                max_seconds=30,
            ),
            TaskDefinition(
                id="independent", objective="Validate", max_iterations=1, max_seconds=30
            ),
        ],
    )
    api_token = "a" * 32
    client = TestClient(create_app(LMStudioProvider(), SecretStr(api_token), store=store))
    headers = {"Authorization": f"Bearer {api_token}"}
    path = f"/runs/{run_id}/tasks/cancel-me/cancel-queued"
    assert client.post(path).status_code == 401
    cancelled = client.post(path, headers=headers)
    assert cancelled.status_code == 200
    body = cancelled.json()
    records = {item["local_id"]: item for item in body["tasks"]}
    assert records["cancel-me"]["status"] == "cancelled"
    assert records["dependent"]["status"] == "queued"
    assert body["ready"] == ["independent"]
    assert body["blocked"] == ["dependent"]
    repeated = client.post(path, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json() == body
    assert (
        sum(
            event["event_type"] == "task.cancelled"
            and event["payload"]
            == {
                "task_id": "cancel-me",
                "reason": "owner_cancelled_before_start",
            }
            for event in store.list_events(run_id)
        )
        == 1
    )

    claim = tasks.claim_ready(run_id, parent)
    assert claim is not None and claim["task"]["local_id"] == "independent"
    running = client.post(f"/runs/{run_id}/tasks/independent/cancel-queued", headers=headers)
    assert running.status_code == 409
    assert "worker is no longer active" in running.json()["detail"]


def test_plan_exposes_safe_block_reasons_without_private_outcome(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "blocked-api.sqlite3")
    identity = store.create_run(
        objective="Plan", acceptance_criteria=[], workspace=None, mode="observe", model="fixture"
    )["id"]
    parent = store.acquire_lease(identity, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        identity,
        parent,
        [
            TaskDefinition(id="inspect", objective="Inspect", max_total_tokens=1),
            TaskDefinition(id="review", objective="Review", dependencies=["inspect"]),
        ],
    )
    claim = tasks.claim_ready(identity, parent)
    tasks.record_outcome(
        identity,
        parent,
        "inspect",
        claim["token"],
        state="blocked",
        result={
            "causes": ["TaskTokenBudgetExceeded"],
            "private_detail": "DO_NOT_EXPOSE_PRIVATE_OUTCOME",
        },
    )
    assert tasks.claim_ready(identity, parent) is None
    api_token = "a" * 32
    client = TestClient(create_app(LMStudioProvider(), SecretStr(api_token), store=store))
    response = client.get(
        f"/runs/{identity}/plan", headers={"Authorization": f"Bearer {api_token}"}
    )
    assert response.status_code == 200
    rows = {row["local_id"]: row for row in response.json()["tasks"]}
    assert rows["inspect"]["blocked_reason"] == "token_budget_exhausted_or_unmeasured"
    assert rows["review"]["blocked_reason"] == "dependency_failed"
    assert rows["inspect"]["budgets"]["max_total_tokens"] == 1
    assert "DO_NOT_EXPOSE_PRIVATE_OUTCOME" not in response.text
    assert "result" not in rows["inspect"]
    assert claim["token"] not in response.text

from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def test_plan_api_returns_typed_tasks_without_worker_authority(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "api.sqlite3")
    run_id = store.create_run(
        objective="Plan", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(run_id, token, [TaskDefinition(id="inspect", objective="Inspect")])
    claim = tasks.claim_ready(run_id, token)
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
        "max_total_tokens": None,
        "max_output_tokens": None,
    }
    assert claim["token"] not in response.text
    assert token not in response.text
    assert "checkpoint" not in task
    assert "result" not in task


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

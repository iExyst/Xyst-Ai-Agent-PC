import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.run_review import RunReviewDecision, review_run
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_worker import FilesystemWorkerError, FilesystemWorkerTimeout
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def candidate(root, *, pending_task=False, uncertain=False):
    target = root / "result.txt"
    target.write_bytes(b"expected")
    store = RunStore(root / "review.sqlite3")
    run_id = store.create_run(
        objective="Produce expected result",
        acceptance_criteria=["File contains expected"],
        workspace=str(root),
        mode="observe",
        model="fixture",
        file_checks=[{"path": "result.txt", "expected_content": "expected"}],
    )["id"]
    token = store.acquire_lease(run_id, "coordinator")
    store.transition(run_id, RunState.PLANNING, owner=token)
    if pending_task:
        TaskStore(store).save_plan(
            run_id, token, [TaskDefinition(id="pending", objective="Inspect")]
        )
    if uncertain:
        store.record_tool_intent(
            run_id,
            token,
            call_id="uncertain",
            tool="write",
            version="1",
            arguments={},
            idempotency_key="uncertain",
        )
    store.append_worker_event(
        run_id,
        token,
        "model.message",
        {
            "message": {
                "role": "assistant",
                "content": "Candidate result produced; awaiting review.",
            }
        },
    )
    store.transition(run_id, RunState.RUNNING, owner=token)
    store.transition(run_id, RunState.VERIFYING, owner=token)
    store.release_lease(run_id, token)
    return store, run_id, target


def decision(store, run_id):
    return {
        "decision": "accept",
        "expected_revision": store.get_run(run_id)["revision"],
        "rationale": "Inspected the objective, candidate and actual file.",
        "objective_confirmed": True,
        "confirmed_criteria": [0],
    }


@pytest.mark.parametrize("error", [FilesystemWorkerError, FilesystemWorkerTimeout])
def test_final_review_worker_failure_does_not_complete(workspace_tmp_path, monkeypatch, error):
    store, run_id, target = candidate(workspace_tmp_path)

    async def failed(*_args, **_kwargs):
        raise error("Worker interrupted")

    monkeypatch.setattr("xaa.runtime.run_review.verify_files_owned", failed)
    token = "x" * 32
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
        response = client.post(
            f"/runs/{run_id}/review",
            headers={"Authorization": f"Bearer {token}"},
            json=decision(store, run_id),
        )
        assert response.status_code == 409
    saved = store.get_run(run_id)
    assert saved["state"] == "verifying" and saved["lease_owner"] is None
    assert target.read_bytes() == b"expected"
    assert not any(e["event_type"] == "run.reviewed" for e in store.list_events(run_id))


async def test_cancelled_final_review_releases_lease_without_accepting(
    workspace_tmp_path, monkeypatch
):
    store, run_id, _ = candidate(workspace_tmp_path)
    entered = asyncio.Event()

    async def waiting(*_args, **_kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr("xaa.runtime.run_review.verify_files_owned", waiting)
    task = asyncio.create_task(
        review_run(store, run_id, RunReviewDecision.model_validate(decision(store, run_id)))
    )
    await asyncio.wait_for(entered.wait(), 1)
    assert store.get_run(run_id)["lease_owner"] is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get_run(run_id)["state"] == "verifying"
    assert store.get_run(run_id)["lease_owner"] is None
    assert not any(e["event_type"] == "run.reviewed" for e in store.list_events(run_id))


def test_user_review_rechecks_files_and_commits_terminal_state_atomically(workspace_tmp_path):
    store, run_id, target = candidate(workspace_tmp_path)
    token = "x" * 32
    headers = {"Authorization": f"Bearer {token}"}
    route = f"/runs/{run_id}/review"
    payload = decision(store, run_id)
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
        assert client.post(route, json=payload).status_code == 401
        for changed in (
            {"expected_revision": payload["expected_revision"] - 1},
            {"objective_confirmed": False},
            {"confirmed_criteria": []},
            {"rationale": " "},
        ):
            assert (
                client.post(route, headers=headers, json={**payload, **changed}).status_code == 409
            )
        assert (
            client.post(route, headers=headers, json={**payload, "actor": "model"}).status_code
            == 422
        )
        active = store.acquire_lease(run_id, "worker")
        assert client.post(route, headers=headers, json=payload).status_code == 409
        store.release_lease(run_id, active)
        target.write_bytes(b"changed")
        assert client.post(route, headers=headers, json=payload).status_code == 409
        assert store.get_run(run_id)["state"] == "verifying"
        target.write_bytes(b"expected")
        response = client.post(route, headers=headers, json=payload)
        assert response.status_code == 200 and response.json()["state"] == "completed"
        assert response.json()["revision"] == payload["expected_revision"] + 1
        assert client.post(route, headers=headers, json=payload).status_code == 409
    reopened = RunStore(store.path)
    assert reopened.get_run(run_id)["lease_owner"] is None
    reviews = [
        event["payload"]
        for event in reopened.list_events(run_id)
        if event["event_type"] == "run.reviewed"
    ]
    assert len(reviews) == 1 and reviews[0]["actor"] == "local-user"
    assert reviews[0]["file_checks_passed"] == 1


def test_final_review_can_retry_an_interrupted_internal_read(workspace_tmp_path):
    store, run_id, _ = candidate(workspace_tmp_path)
    token = store.acquire_lease(run_id, "old-review")
    store.record_tool_intent(
        run_id,
        token,
        call_id="lost-review",
        tool="workspace.read_file",
        version="1",
        arguments={"path": "result.txt"},
        idempotency_key="lost-review",
        internal=True,
    )
    store.release_lease(run_id, token)
    with TestClient(create_app(LMStudioProvider(), SecretStr("x" * 32), store=store)) as client:
        response = client.post(
            f"/runs/{run_id}/review",
            headers={"Authorization": f"Bearer {'x' * 32}"},
            json=decision(store, run_id),
        )
        assert response.status_code == 200
    assert store.get_run(run_id)["state"] == "completed"
    assert not store.unresolved_tool_calls(run_id)
    assert not any(m["role"] == "tool" for m in store.conversation_messages(run_id))


@pytest.mark.parametrize("pending_task,uncertain", [(True, False), (False, True)])
def test_final_review_cannot_bypass_specialists_or_uncertain_actions(
    workspace_tmp_path, pending_task, uncertain
):
    store, run_id, _ = candidate(workspace_tmp_path, pending_task=pending_task, uncertain=uncertain)
    token = "x" * 32
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
        response = client.post(
            f"/runs/{run_id}/review",
            headers={"Authorization": f"Bearer {token}"},
            json=decision(store, run_id),
        )
        assert response.status_code == 409
        assert store.get_run(run_id)["state"] == "verifying"
        rejected = client.post(
            f"/runs/{run_id}/review",
            headers={"Authorization": f"Bearer {token}"},
            json={
                **decision(store, run_id),
                "decision": "reject",
                "objective_confirmed": False,
                "confirmed_criteria": [],
            },
        )
        assert rejected.status_code == 200 and rejected.json()["state"] == "failed"
        if pending_task:
            assert TaskStore(store).records(run_id)[0]["status"] == "cancelled"

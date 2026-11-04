import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.task_review import TaskReviewDecision, review_candidate
from xaa.runtime.tool_worker import FilesystemWorkerError, FilesystemWorkerTimeout
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def setup_candidate(root):
    target = root / "evidence.txt"
    target.write_bytes(b"actual evidence")
    store = RunStore(root / "review.sqlite3")
    run_id = store.create_run(
        objective="Inspect",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "coordinator")
    store.transition(run_id, RunState.PLANNING, owner=parent)
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="inspect",
                objective="Inspect file",
                acceptance_criteria=["Reports actual evidence"],
            ),
            TaskDefinition(id="review", objective="Review", dependencies=["inspect"]),
        ],
    )
    claim = tasks.claim_ready(run_id, parent)
    evidence = WorkspaceExecutor(store).read_file(
        run_id, parent, "source", {"path": "evidence.txt"}, idempotency_key="source", internal=True
    )
    store.append_worker_event(
        run_id,
        parent,
        "task.tool.result",
        {"task_id": "inspect", "call_id": "model-read", "journal_id": "source", "result": evidence},
    )
    tasks.record_outcome(
        run_id,
        parent,
        "inspect",
        claim["token"],
        state="verifying",
        result={"candidate": "The file says actual evidence."},
    )
    store.transition(run_id, RunState.RUNNING, owner=parent)
    store.transition(run_id, RunState.VERIFYING, owner=parent)
    store.release_lease(run_id, parent)
    return store, run_id, target


@pytest.mark.parametrize("error", [FilesystemWorkerError, FilesystemWorkerTimeout])
def test_specialist_review_worker_failure_preserves_pending_candidate(
    workspace_tmp_path, monkeypatch, error
):
    store, run_id, _ = setup_candidate(workspace_tmp_path)

    async def interrupted(*_args, **_kwargs):
        raise error("Interrupted")

    monkeypatch.setattr("xaa.runtime.task_review.execute_filesystem", interrupted)
    with TestClient(create_app(LMStudioProvider(), SecretStr("x" * 32), store=store)) as client:
        response = client.post(
            f"/runs/{run_id}/tasks/inspect/review",
            headers={"Authorization": f"Bearer {'x' * 32}"},
            json={
                "decision": "accept",
                "rationale": "Inspect evidence",
                "confirmed_criteria": [0],
                "evidence_call_ids": ["source"],
            },
        )
        assert response.status_code == 409
    assert TaskStore(store).records(run_id)[0]["status"] == "verifying"
    assert store.get_run(run_id)["lease_owner"] is None
    assert not any(e["event_type"] == "task.reviewed" for e in store.list_events(run_id))


async def test_cancelled_specialist_review_releases_lease(workspace_tmp_path, monkeypatch):
    store, run_id, _ = setup_candidate(workspace_tmp_path)
    started = asyncio.Event()

    async def waiting(*_args, **_kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr("xaa.runtime.task_review.execute_filesystem", waiting)
    task = asyncio.create_task(
        review_candidate(
            store,
            run_id,
            "inspect",
            TaskReviewDecision(
                decision="accept",
                rationale="Inspect evidence",
                confirmed_criteria=[0],
                evidence_call_ids=["source"],
            ),
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get_run(run_id)["lease_owner"] is None
    assert TaskStore(store).records(run_id)[0]["status"] == "verifying"


def test_specialist_review_can_retry_an_interrupted_internal_read(workspace_tmp_path):
    store, run_id, _ = setup_candidate(workspace_tmp_path)
    token = store.acquire_lease(run_id, "old-review")
    store.record_tool_intent(
        run_id,
        token,
        call_id="lost-review",
        tool="workspace.read_file",
        version="1",
        arguments={"path": "evidence.txt"},
        idempotency_key="lost-review",
        internal=True,
    )
    store.release_lease(run_id, token)
    with TestClient(create_app(LMStudioProvider(), SecretStr("x" * 32), store=store)) as client:
        response = client.post(
            f"/runs/{run_id}/tasks/inspect/review",
            headers={"Authorization": f"Bearer {'x' * 32}"},
            json={
                "decision": "accept",
                "rationale": "Inspect fresh evidence",
                "confirmed_criteria": [0],
                "evidence_call_ids": ["source"],
            },
        )
        assert response.status_code == 200
    assert TaskStore(store).records(run_id)[0]["status"] == "completed"
    assert not store.unresolved_tool_calls(run_id)
    assert not store.conversation_messages(run_id)


def test_review_requires_explicit_criteria_and_unchanged_actual_evidence(workspace_tmp_path):
    store, run_id, target = setup_candidate(workspace_tmp_path)
    token = "x" * 32
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/runs/{run_id}/tasks/inspect"
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
        assert client.get(base + "/candidate").status_code == 401
        candidate = client.get(base + "/candidate", headers=headers).json()
        assert candidate["candidate"] == "The file says actual evidence."
        assert candidate["evidence"][0]["excerpt"] == "actual evidence"
        decision = {
            "decision": "accept",
            "rationale": "Compared the file with the criterion.",
            "confirmed_criteria": [0],
            "evidence_call_ids": ["source"],
        }
        assert client.post(base + "/review", json=decision).status_code == 401
        assert (
            client.post(
                base + "/review", headers=headers, json={**decision, "confirmed_criteria": []}
            ).status_code
            == 409
        )
        assert (
            client.post(
                base + "/review",
                headers=headers,
                json={**decision, "evidence_call_ids": ["other-task"]},
            ).status_code
            == 409
        )
        assert (
            client.post(
                base + "/review", headers=headers, json={**decision, "actor": "model"}
            ).status_code
            == 422
        )
        active = store.acquire_lease(run_id, "active-worker")
        assert client.post(base + "/review", headers=headers, json=decision).status_code == 409
        store.release_lease(run_id, active)
        target.write_bytes(b"changed after specialist read")
        assert client.post(base + "/review", headers=headers, json=decision).status_code == 409
        assert TaskStore(store).records(run_id)[0]["status"] == "verifying"
        target.write_bytes(b"actual evidence")
        response = client.post(base + "/review", headers=headers, json=decision)
        assert response.status_code == 200
        assert response.json()["ready"] == ["review"]
        assert response.json()["tasks"][0]["status"] == "completed"
        assert store.get_run(run_id)["state"] == "verifying"
        assert client.post(base + "/review", headers=headers, json=decision).status_code == 409
    reviews = [
        event["payload"]
        for event in store.list_events(run_id)
        if event["event_type"] == "task.reviewed"
    ]
    assert len(reviews) == 1
    assert reviews[0]["actor"] == "local-user"
    assert reviews[0]["checked_evidence"][0]["path"] == "evidence.txt"


def test_user_can_reject_candidate_without_claiming_verification(workspace_tmp_path):
    store, run_id, _ = setup_candidate(workspace_tmp_path)
    token = "x" * 32
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
        response = client.post(
            f"/runs/{run_id}/tasks/inspect/review",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "decision": "reject",
                "rationale": "Evidence does not satisfy the requested scope.",
            },
        )
        assert response.status_code == 200
        assert response.json()["tasks"][0]["status"] == "failed"
        assert response.json()["blocked"] == ["review"]


def test_accepted_prerequisite_enables_supervised_dependent_dispatch(workspace_tmp_path):
    store, run_id, _ = setup_candidate(workspace_tmp_path)

    class DependentReader(LMStudioProvider):
        def __init__(self):
            super().__init__()
            self.finished = threading.Event()

        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            if messages[-1]["role"] == "user":
                assignment = json.loads(messages[-1]["content"])
                assert assignment["prerequisites_untrusted"][0]["task_id"] == "inspect"
                assert (
                    assignment["prerequisites_untrusted"][0]["file_evidence"][0]["path"]
                    == "evidence.txt"
                )
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "independent-read",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"evidence.txt"}',
                            },
                        }
                    ],
                }
            else:
                assert json.loads(messages[-1]["content"])["text"] == "actual evidence"
                self.finished.set()
                message = {"role": "assistant", "content": "Independently read actual evidence."}
            return {"choices": [{"message": message}]}

    provider = DependentReader()
    token = "x" * 32
    headers = {"Authorization": f"Bearer {token}"}
    dispatch = f"/runs/{run_id}/plan/dispatch"
    with TestClient(create_app(provider, SecretStr(token), store=store)) as client:
        assert client.post(dispatch).status_code == 401
        assert client.post(dispatch, headers=headers).status_code == 409
        accepted = client.post(
            f"/runs/{run_id}/tasks/inspect/review",
            headers=headers,
            json={
                "decision": "accept",
                "rationale": "Evidence matches.",
                "confirmed_criteria": [0],
                "evidence_call_ids": ["source"],
            },
        )
        assert accepted.status_code == 200
        response = client.post(dispatch, headers=headers)
        assert response.status_code == 202
        assert response.json()["state"] == "running"
        assert provider.finished.wait(3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            saved = store.get_run(run_id)
            if saved["state"] == "verifying" and saved["lease_owner"] is None:
                break
            time.sleep(0.01)
        assert store.get_run(run_id)["state"] == "verifying"
        assert TaskStore(store).records(run_id)[1]["status"] == "verifying"
        assert store.get_run(run_id)["lease_owner"] is None
        assert client.post(dispatch, headers=headers).status_code == 409
        assert store.conversation_messages(run_id) == []

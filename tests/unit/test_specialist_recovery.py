import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
from xaa.runtime.supervisor import RunSupervisor
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def test_parent_expiry_fences_live_child_lease_and_blocks_replay(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "recovery.sqlite3")
    run_id = store.create_run(
        objective="Recover", acceptance_criteria=[], workspace=None, mode="observe", model="fixture"
    )["id"]
    parent = store.acquire_lease(run_id, "old-parent")
    store.transition(run_id, RunState.PLANNING, owner=parent)
    tasks = TaskStore(store)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="task", objective="Inspect")])
    claim = tasks.claim_ready(run_id, parent)
    assert store.recover_abandoned_runs() == []  # Live parent/child remain untouched.
    with store.connection() as connection:
        connection.execute(
            "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run_id,)
        )
    reopened = RunStore(store.path)
    assert reopened.recover_abandoned_runs() == []
    assert reopened.get_run(run_id)["state"] == "blocked"
    recovered_tasks = TaskStore(reopened)
    task = recovered_tasks.records(run_id)[0]
    assert task["status"] == "blocked"
    assert task["blocked_reason"] == "worker_lost_reconciliation_required"
    summary = recovered_tasks.recovery_summary(run_id, "task")
    assert summary["safe_to_resume"] is False
    token = "restart-recovery-test-token-long-enough"
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=reopened)) as client:
        response = client.get(
            f"/runs/{run_id}/tasks/task/recovery",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["safe_to_resume"] is False
    with reopened.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM task_leases").fetchone()[0] == 0
    reopened.transition(run_id, RunState.QUEUED)
    replacement = reopened.acquire_lease(run_id, "new-parent")
    with pytest.raises(PermissionError):
        TaskStore(reopened).record_outcome(
            run_id,
            replacement,
            "task",
            claim["token"],
            state="verifying",
            result={"candidate": "stale"},
        )
    assert TaskStore(reopened).claim_ready(run_id, replacement) is None


async def test_dispatch_cancelled_before_coroutine_start_releases_reserved_ownership(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "before-start.sqlite3")
    run_id = store.create_run(
        objective="Dispatch",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    token = store.acquire_lease(run_id, "reserved")
    for state in (RunState.PLANNING, RunState.RUNNING, RunState.VERIFYING, RunState.RUNNING):
        store.transition(run_id, state, owner=token)
    provider = LMStudioProvider()
    supervisor = RunSupervisor(Coordinator(store, provider))
    try:
        assert supervisor.submit_plan(run_id, token)
        await supervisor.stop(run_id)
        assert store.get_run(run_id)["lease_owner"] is None
        assert store.get_run(run_id)["state"] == "paused"
    finally:
        await supervisor.close()
        await provider.close()


@pytest.mark.parametrize("action,task_state", [("pause", "blocked"), ("cancel", "cancelled")])
def test_api_stop_cancels_both_specialists_and_fences_results(
    workspace_tmp_path, action, task_state
):
    class WaitingSpecialists(LMStudioProvider):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.stopped = threading.Event()
            self.started_count = 0
            self.stopped_count = 0

        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            if messages[0]["content"].startswith("You are "):
                self.started_count += 1
                if self.started_count == 2:
                    self.started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.stopped_count += 1
                    if self.stopped_count == 2:
                        self.stopped.set()
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "dispatch",
                                    "type": "function",
                                    "function": {"name": "plan_run_ready", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }

    store = RunStore(workspace_tmp_path / f"{action}.sqlite3")
    run_id = store.create_run(
        objective="Execute plan",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "planner")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id=name, objective=name, max_iterations=2) for name in ("a", "b")],
    )
    store.release_lease(run_id, parent)
    provider = WaitingSpecialists()
    token = "x" * 32
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(provider, SecretStr(token), store=store)) as client:
        assert provider.started.wait(3)
        response = client.post(f"/runs/{run_id}/{action}", headers=headers)
        assert response.status_code == 200
        assert provider.stopped.wait(2)
        assert all(row["status"] == task_state for row in tasks.records(run_id))
        with store.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM task_leases").fetchone()[0] == 0
        assert store.get_run(run_id)["lease_owner"] is None
        assert not any(event["event_type"] == "task.result" for event in store.list_events(run_id))
        if action == "pause":
            assert client.post(f"/runs/{run_id}/resume", headers=headers).status_code == 200
            deadline = time.monotonic() + 3
            while store.get_run(run_id)["state"] != "blocked" and time.monotonic() < deadline:
                time.sleep(0.01)
            assert store.get_run(run_id)["state"] == "blocked"
            assert provider.started_count == 2  # An uncertain dispatch is not replayed.

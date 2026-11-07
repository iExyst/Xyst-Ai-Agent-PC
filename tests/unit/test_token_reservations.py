import time

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.providers.tokenization import PromptCount
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore, TokenBudgetExceeded
from xaa.storage.tasks import TaskStore


def create(root, limit=200):
    store = RunStore(root / "reservations.sqlite3")
    run = store.create_run(
        objective="Answer",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model="fixture",
        max_total_tokens=limit,
        max_output_tokens=30,
        max_iterations=3,
    )
    return store, run["id"]


def start(store, run, token, identity):
    store.append_worker_event(run, token, "model.inference.started", {"inference_id": identity})


def answer(store, run, token, identity, total):
    store.append_worker_event(
        run,
        token,
        "model.message",
        {
            "inference_id": identity,
            "message": {"role": "assistant", "content": "ok"},
            "usage": {"prompt_tokens": total - 1, "completion_tokens": 1, "total_tokens": total},
        },
    )


def incomplete(store, run, token, identity, total):
    store.append_worker_event(
        run,
        token,
        "model.incomplete",
        {
            "inference_id": identity,
            "reason": "prompt_count_mismatch",
            "response_discarded": True,
            "usage": {"prompt_tokens": total - 1, "completion_tokens": 1, "total_tokens": total},
        },
    )


def test_reservations_survive_reopen_and_release_only_for_durable_measured_answer(
    workspace_tmp_path,
):
    store, run = create(workspace_tmp_path)
    token = store.acquire_lease(run, "worker")
    start(store, run, token, "first")
    store.reserve_tokens(run, token, "first", 100, 30)
    store = RunStore(store.path)
    report = store.usage_report(run)
    assert report["outstanding_reserved_tokens"] == 130
    assert report["outstanding_reservations"] == 1
    assert report["total_tokens"] == 0
    start(store, run, token, "second")
    with pytest.raises(TokenBudgetExceeded):
        store.reserve_tokens(run, token, "second", 50, 30)
    # Releasing 20 unused tokens permits an exact-boundary reservation.
    answer(store, run, token, "first", 110)
    report = store.usage_report(run)
    assert report["outstanding_reserved_tokens"] == 0
    assert report["outstanding_reservations"] == 0
    assert report["total_tokens"] == 110
    store.reserve_tokens(run, token, "second", 60, 30)
    with pytest.raises(ValueError, match="already"):
        store.reserve_tokens(run, token, "second", 1, 1)
    assert len([e for e in store.list_events(run) if e["event_type"] == "tokens.reserved"]) == 2


def test_old_unknown_attempt_cannot_be_hidden_by_new_reservation(workspace_tmp_path):
    store, run = create(workspace_tmp_path)
    token = store.acquire_lease(run, "worker")
    start(store, run, token, "unknown")
    start(store, run, token, "current")
    with pytest.raises(TokenBudgetExceeded):
        store.reserve_tokens(run, token, "current", 1, 1)


def test_measured_discarded_mismatch_releases_hold_and_counts_tokens(workspace_tmp_path):
    store, run = create(workspace_tmp_path)
    token = store.acquire_lease(run, "worker")
    start(store, run, token, "mismatch")
    store.reserve_tokens(run, token, "mismatch", 100, 30)
    incomplete(store, run, token, "mismatch", 110)
    report = store.usage_report(run)
    assert report["total_tokens"] == 110
    assert report["unmeasured_calls"] == 0
    assert report["outstanding_reservations"] == 0
    assert report["outstanding_reserved_tokens"] == 0


@pytest.mark.parametrize("limit,predictions", [(129, 0), (130, 1)])
async def test_coordinator_reserves_before_http_or_blocks_without_prediction(
    workspace_tmp_path, limit, predictions
):
    store, run = create(workspace_tmp_path, limit)

    class Provider(LMStudioProvider):
        def __init__(self):
            super().__init__(exact_context=True)
            self.calls = 0

        async def diagnose_tools(self, model, *, request=None):
            return True

        async def _count_prompt_owned(self, request):
            return PromptCount(
                model=request.model, prompt_tokens=100, context_length=4096, sdk_version="fixture"
            )

        async def _chat(self, **kwargs):
            self.calls += 1
            assert any(e["event_type"] == "tokens.reserved" for e in store.list_events(run))
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            }

    provider = Provider()
    try:
        await Coordinator(store, provider).execute(run)
        assert provider.calls == predictions
        assert store.get_run(run)["state"] == ("verifying" if predictions else "blocked")
    finally:
        await provider.close()


@pytest.mark.parametrize("allowance,predictions", [(129, 0), (130, 1)])
async def test_specialist_reservation_enforces_own_allowance_before_http(
    workspace_tmp_path, allowance, predictions
):
    store, run = create(workspace_tmp_path)
    token = store.acquire_lease(run, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run,
        token,
        [
            TaskDefinition(
                id="inspect",
                objective="Inspect",
                max_iterations=1,
                max_total_tokens=allowance,
                max_output_tokens=30,
            )
        ],
    )

    class Provider(LMStudioProvider):
        def __init__(self):
            super().__init__(exact_context=True)
            self.calls = 0

        async def _count_prompt_owned(self, request):
            return PromptCount(
                model=request.model, prompt_tokens=100, context_length=4096, sdk_version="fixture"
            )

        async def _chat(self, **kwargs):
            self.calls += 1
            return {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "candidate"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            }

    provider = Provider()
    try:
        await SpecialistRunner(store, provider).run_ready(
            run, token, deadline=time.monotonic() + 10
        )
        assert provider.calls == predictions
        task = tasks.records(run)[0]
        assert task["status"] == ("verifying" if predictions else "blocked")
        if not predictions:
            assert task["blocked_reason"] == "token_budget_exhausted_or_unmeasured"
        else:
            events = store.list_events(run)
            hold = next(e["payload"] for e in events if e["event_type"] == "tokens.reserved")
            reply = next(e["payload"] for e in events if e["event_type"] == "task.model.message")
            assert hold["inference_id"] == reply["inference_id"]
            assert hold["task_id"] == "inspect"
    finally:
        await provider.close()


async def test_authenticated_usage_api_reports_saved_holds_without_prompt_content(
    workspace_tmp_path,
):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from xaa.api.app import create_app

    store, run = create(workspace_tmp_path)
    token = store.acquire_lease(run, "parent")
    start(store, run, token, "pending")
    store.reserve_tokens(run, token, "pending", 100, 30)
    provider = LMStudioProvider()
    try:
        app = create_app(provider, SecretStr("x" * 32), store=store)
        client = TestClient(app)
        assert client.get(f"/runs/{run}/usage").status_code == 401
        response = client.get(f"/runs/{run}/usage", headers={"Authorization": "Bearer " + "x" * 32})
        assert response.status_code == 200
        assert response.json() == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "unmeasured_calls": 1,
            "outstanding_reserved_tokens": 130,
            "outstanding_reservations": 1,
        }
    finally:
        await provider.close()

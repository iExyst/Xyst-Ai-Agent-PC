import json
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore, TokenBudgetExceeded
from xaa.storage.tasks import TaskStore


class DiagnosticProvider(LMStudioProvider):
    def __init__(self, missing=False, interrupted=False):
        super().__init__()
        self.calls = 0
        self.missing = missing
        self.interrupted = interrupted

    async def chat(
        self,
        *,
        model,
        messages,
        tools=None,
        max_output_tokens=None,
        on_admission=None,
        require_exact_context=False,
    ):
        self.calls += 1
        if require_exact_context:
            assert on_admission is not None
            on_admission(2, max_output_tokens or 512)
        if self.interrupted:
            raise ConnectionError("fixture disconnect")
        if tools and tools[0]["function"]["name"] == "xaa_echo":
            nonce = re.search(r"value ([0-9a-f]{32})", messages[0]["content"]).group(1)
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "echo",
                        "type": "function",
                        "function": {"name": "xaa_echo", "arguments": json.dumps({"value": nonce})},
                    }
                ],
            }
        elif messages[-1]["role"] == "tool":
            message = {"role": "assistant", "content": messages[-1]["content"]}
        else:
            message = {"role": "assistant", "content": "Candidate"}
        response = {"choices": [{"message": message}], "private_reasoning": "MUST_NOT_PERSIST"}
        if not self.missing:
            response["usage"] = {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
        return response


def create(store, root, limit):
    return store.create_run(
        objective="Inspect workspace",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model="fixture",
        max_total_tokens=limit,
    )["id"]


@pytest.mark.parametrize(
    "limit,expected_calls,expected_tokens,state",
    [(1, 1, 3, "blocked"), (6, 2, 6, "blocked"), (7, 3, 9, "blocked"), (10, 3, 9, "verifying")],
)
async def test_diagnostic_tokens_gate_each_request_and_workspace_loop(
    workspace_tmp_path, limit, expected_calls, expected_tokens, state
):
    store = RunStore(workspace_tmp_path / "diagnostic.sqlite3")
    identity = create(store, workspace_tmp_path, limit)
    provider = DiagnosticProvider()
    try:
        await Coordinator(store, provider).execute(identity)
    finally:
        await provider.close()
    assert provider.calls == expected_calls
    assert store.get_run(identity)["state"] == state
    assert RunStore(store.path).token_usage(identity)["total_tokens"] == expected_tokens
    assert store.token_usage(identity)["unmeasured_calls"] == 0
    assert store.inference_attempts(identity) == max(0, expected_calls - 2)
    events = store.list_events(identity)
    assert "MUST_NOT_PERSIST" not in json.dumps(events)
    diagnostic = [e for e in events if e["event_type"].startswith("diagnostic.")]
    assert len(diagnostic) == 2 * min(2, expected_calls)
    assert all("message" not in e["payload"] for e in diagnostic)


@pytest.mark.parametrize("interrupted", [False, True])
async def test_unknown_diagnostic_usage_survives_reopen_and_blocks_retry(
    workspace_tmp_path, interrupted
):
    store = RunStore(workspace_tmp_path / "unknown.sqlite3")
    identity = create(store, workspace_tmp_path, 100)
    provider = DiagnosticProvider(missing=True, interrupted=interrupted)
    try:
        if interrupted:
            with pytest.raises(ConnectionError):
                await Coordinator(store, provider).execute(identity)
        else:
            await Coordinator(store, provider).execute(identity)
    finally:
        await provider.close()
    assert provider.calls == 1
    reopened = RunStore(store.path)
    assert reopened.token_usage(identity)["unmeasured_calls"] == 1
    # Gate remains closed when a fresh lease is acquired after explicit resume.
    if interrupted:
        assert reopened.get_run(identity)["state"] == "failed"
    else:
        reopened.transition(identity, RunState.QUEUED)
        token = reopened.acquire_lease(identity, "new-worker")
        with pytest.raises(TokenBudgetExceeded):
            reopened.check_token_budget(identity, token, before_inference=True)


async def test_cached_diagnostic_has_no_new_inference_charge(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "cache.sqlite3")
    provider = DiagnosticProvider()
    try:
        first = create(store, workspace_tmp_path, 100)
        await Coordinator(store, provider).execute(first)
        second = create(store, workspace_tmp_path, 100)
        await Coordinator(store, provider).execute(second)
        assert provider.calls == 4
        assert store.token_usage(first)["total_tokens"] == 9
        assert store.token_usage(second)["total_tokens"] == 3
        assert not any(e["event_type"].startswith("diagnostic.") for e in store.list_events(second))
    finally:
        await provider.close()


async def test_pre_inference_reservations_cover_diagnostics_and_parent_requests(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "diagnostic-reservations.sqlite3")
    run = store.create_run(
        objective="Inspect workspace",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_total_tokens=3000,
        pre_inference_token_reservations=True,
    )
    provider = DiagnosticProvider()
    try:
        await Coordinator(store, provider).execute(run["id"])
    finally:
        await provider.close()
    events = store.list_events(run["id"])
    starts = {
        event["payload"]["inference_id"]
        for event in events
        if event["event_type"] in {"diagnostic.inference.started", "model.inference.started"}
    }
    holds = {
        event["payload"]["inference_id"]
        for event in events
        if event["event_type"] == "tokens.reserved"
    }
    assert len(starts) == provider.calls
    assert holds == starts
    assert store.usage_report(run["id"])["outstanding_reservations"] == 0


def test_concurrent_reservations_cannot_exceed_shared_run_token_budget(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "concurrent-reservations.sqlite3")
    run_id = store.create_run(
        objective="Share a hard token ceiling",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_total_tokens=100,
        pre_inference_token_reservations=True,
    )["id"]
    owner = store.acquire_lease(run_id, "coordinator")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(id=task_id, objective=task_id, max_iterations=1, max_seconds=10)
            for task_id in ("a", "b")
        ],
    )
    claims = {task_id: tasks.claim_ready(run_id, owner) for task_id in ("a", "b")}
    assert all(claim is not None for claim in claims.values())
    inference_ids = {
        task_id: tasks.reserve_inference(run_id, owner, task_id, claims[task_id]["token"])
        for task_id in ("a", "b")
    }
    barrier = Barrier(len(inference_ids))

    def reserve(task_id: str) -> bool:
        barrier.wait(timeout=5)
        try:
            tasks.reserve_tokens(
                run_id,
                owner,
                inference_ids[task_id],
                40,
                20,
                task_id=task_id,
                task_token=claims[task_id]["token"],
            )
        except TokenBudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=len(inference_ids)) as executor:
        admitted = list(executor.map(reserve, inference_ids))

    assert sum(admitted) == 1
    report = store.usage_report(run_id)
    assert report["outstanding_reservations"] == 1
    assert report["outstanding_reserved_tokens"] == 60


async def test_explicit_specialist_dispatch_obeys_diagnostic_threshold(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "dispatch.sqlite3")
    identity = create(store, workspace_tmp_path, 1)
    token = store.acquire_lease(identity, "dispatcher")
    store.transition(identity, RunState.PLANNING, owner=token)
    store.transition(identity, RunState.RUNNING, owner=token)
    provider = DiagnosticProvider()
    try:
        await Coordinator(store, provider).continue_plan(identity, token)
    finally:
        await provider.close()
    assert store.get_run(identity)["state"] == "blocked"
    assert provider.calls == 1
    assert store.token_usage(identity)["total_tokens"] == 3
    assert not any(e["event_type"] == "task.inference.started" for e in store.list_events(identity))

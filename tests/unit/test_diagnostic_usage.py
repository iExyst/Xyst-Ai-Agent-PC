import json
import re

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore, TokenBudgetExceeded


class DiagnosticProvider(LMStudioProvider):
    def __init__(self, missing=False, interrupted=False):
        super().__init__()
        self.calls = 0
        self.missing = missing
        self.interrupted = interrupted

    async def chat(self, *, model, messages, tools=None, max_output_tokens=None):
        self.calls += 1
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

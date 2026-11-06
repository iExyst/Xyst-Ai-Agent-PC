import json

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore, TokenBudgetExceeded


def create(root, limit):
    store = RunStore(root / "tokens.sqlite3")
    run = store.create_run(
        objective="Create output.txt",
        acceptance_criteria=[],
        workspace=str(root),
        mode="workspace",
        model="fixture",
        max_total_tokens=limit,
    )
    return store, run["id"]


async def test_configured_pre_inference_budget_reserves_before_parent_request(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "pre-inference-budget.sqlite3")
    run = store.create_run(
        objective="Return a short answer",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_total_tokens=500,
        max_output_tokens=64,
        pre_inference_token_reservations=True,
    )

    class Provider(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(
            self,
            *,
            model,
            messages,
            tools=None,
            on_admission=None,
            require_exact_context=False,
            max_output_tokens=None,
        ):
            assert require_exact_context is True
            assert max_output_tokens == 64
            assert on_admission is not None
            on_admission(30, max_output_tokens)
            return {
                "choices": [{"message": {"role": "assistant", "content": "Done."}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35},
            }

    provider = Provider()
    try:
        await Coordinator(store, provider).execute(run["id"])
    finally:
        await provider.close()
    events = store.list_events(run["id"])
    reservation = next(event for event in events if event["event_type"] == "tokens.reserved")
    assert reservation["payload"]["reserved_tokens"] == 94
    assert store.token_usage(run["id"])["total_tokens"] == 35
    assert store.usage_report(run["id"])["outstanding_reserved_tokens"] == 0


@pytest.mark.parametrize("usage,blocked", [(None, True), (11, True), (10, False), (9, False)])
async def test_threshold_blocks_proposed_write_before_effect(workspace_tmp_path, usage, blocked):
    store, identity = create(workspace_tmp_path, 10)

    class Provider(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            response = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "create",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_create_file",
                                        "arguments": json.dumps(
                                            {"path": "output.txt", "content": "created"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
            if usage is not None:
                response["usage"] = {
                    "prompt_tokens": usage - 1,
                    "completion_tokens": 1,
                    "total_tokens": usage,
                }
            return response

    provider = Provider()
    try:
        await Coordinator(store, provider).execute(identity)
    finally:
        await provider.close()
    assert (workspace_tmp_path / "output.txt").exists() is (not blocked)
    events = store.list_events(identity)
    if blocked:
        assert not any(e["event_type"] == "tool.intended" for e in events)
        assert store.get_run(identity)["state"] == "blocked"
        assert any(
            e["event_type"] == "run.state_changed"
            and e["payload"].get("reason") == "token_budget_exhausted_or_unmeasured"
            for e in events
        )
    assert RunStore(store.path).get_run(identity)["limits"]["max_total_tokens"] == 10


def test_shared_usage_and_interrupted_attempt_survive_reopen(workspace_tmp_path):
    store, identity = create(workspace_tmp_path, 10)
    token = store.acquire_lease(identity, "worker")
    store.append_worker_event(identity, token, "task.inference.started", {"task_id": "child"})
    with pytest.raises(TokenBudgetExceeded):
        store.check_token_budget(identity, token, before_inference=True)
    store.append_worker_event(
        identity,
        token,
        "task.model.message",
        {
            "task_id": "child",
            "message": {"role": "assistant", "content": "candidate"},
            "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10},
        },
    )
    reopened = RunStore(store.path)
    reopened.check_token_budget(identity, token, before_inference=False)
    with pytest.raises(TokenBudgetExceeded):
        reopened.check_token_budget(identity, token, before_inference=True)


@pytest.mark.parametrize("limit", [True, 0, -1, 100_000_001, 1.5])
def test_invalid_threshold_rejected(workspace_tmp_path, limit):
    with pytest.raises(ValueError):
        create(workspace_tmp_path, limit)

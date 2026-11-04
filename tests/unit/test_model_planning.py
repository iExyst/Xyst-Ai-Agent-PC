import json

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.planning import save_model_plan
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


async def test_model_plan_round_trip_is_saved_and_replayable(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "plan.sqlite3")
    run_id = store.create_run(
        objective="Plan analysis and review",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    proposal = {
        "tasks": [
            {
                "id": "inspect",
                "objective": "Inspect project",
                "role": "researcher",
                "max_iterations": 2,
                "dependencies": [],
                "acceptance_criteria": ["Evidence collected"],
            },
            {
                "id": "review",
                "objective": "Review evidence",
                "role": "reviewer",
                "max_iterations": 2,
                "dependencies": ["inspect"],
                "acceptance_criteria": ["Evidence inspected"],
            },
        ]
    }

    class Planner(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            if messages[-1]["role"] == "user":
                assert any(t["function"]["name"] == "plan_save" for t in tools)
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "plan-1",
                            "type": "function",
                            "function": {"name": "plan_save", "arguments": json.dumps(proposal)},
                        }
                    ],
                }
            else:
                assert not any(t["function"]["name"] == "plan_save" for t in tools)
                result = json.loads(messages[-1]["content"])
                assert result["ready"] == ["inspect"]
                message = {"role": "assistant", "content": "Plan saved; execution pending."}
            return {"choices": [{"message": message}]}

    provider = Planner()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    tasks = TaskStore(RunStore(store.path))
    assert tasks.readiness(run_id) == (["inspect"], [])
    assert all(row["status"] == "queued" for row in tasks.records(run_id))
    assert not store.unresolved_tool_calls(run_id)
    history = store.conversation_messages(run_id)
    assert history[1]["tool_call_id"] == "plan-1"
    assert json.loads(history[1]["content"])["status"] == "saved"


@pytest.mark.parametrize(
    "invalid,code",
    [
        ({"tasks": []}, "invalid_schema"),
        (
            {
                "tasks": [
                    {
                        "id": "edit",
                        "objective": "Edit",
                        "dependencies": [],
                        "acceptance_criteria": ["Edited"],
                        "permissions": ["workspace.write"],
                        "max_iterations": 1,
                    }
                ]
            },
            "permission_denied",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "large",
                        "objective": "Inspect",
                        "dependencies": [],
                        "acceptance_criteria": ["Evidence"],
                        "max_iterations": 100,
                    }
                ]
            },
            "invalid_graph_or_budget",
        ),
    ],
)
async def test_model_can_correct_rejected_plan_without_uncertain_actions(
    workspace_tmp_path, invalid, code
):
    store = RunStore(workspace_tmp_path / "correct.sqlite3")
    run_id = store.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_iterations=4,
    )["id"]
    valid = {
        "tasks": [
            {
                "id": "inspect",
                "objective": "Inspect",
                "dependencies": [],
                "acceptance_criteria": ["Actual evidence"],
                "max_iterations": 1,
            }
        ]
    }

    class CorrectingPlanner(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            if messages[-1]["role"] == "user":
                proposal, identity = invalid, "invalid"
            elif messages[-1]["tool_call_id"] == "invalid":
                assert TaskStore(store).records(run_id) == []
                result = json.loads(messages[-1]["content"])
                assert result["status"] == "rejected" and result["code"] == code
                assert any(tool["function"]["name"] == "plan_save" for tool in tools)
                proposal, identity = valid, "corrected"
            else:
                return {
                    "choices": [
                        {"message": {"role": "assistant", "content": "Corrected plan saved."}}
                    ]
                }
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": identity,
                                    "type": "function",
                                    "function": {
                                        "name": "plan_save",
                                        "arguments": json.dumps(proposal),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    provider = CorrectingPlanner()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "verifying"
    assert TaskStore(store).readiness(run_id) == (["inspect"], [])
    assert store.inference_attempts(run_id) == 3
    assert not store.unresolved_tool_calls(run_id)
    assert [
        message["tool_call_id"]
        for message in store.conversation_messages(run_id)
        if message["role"] == "tool"
    ] == ["invalid", "corrected"]


async def test_repeated_plan_rejections_stop_at_shared_budget(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "bounded.sqlite3")
    run_id = store.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_iterations=3,
    )["id"]

    class InvalidPlanner(LMStudioProvider):
        calls = 0

        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, **kwargs):
            self.calls += 1
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"invalid-{self.calls}",
                                    "type": "function",
                                    "function": {"name": "plan_save", "arguments": '{"tasks":[]}'},
                                }
                            ],
                        }
                    }
                ]
            }

    provider = InvalidPlanner()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    assert provider.calls == 3
    assert store.get_run(run_id)["state"] == "blocked"
    assert store.inference_attempts(run_id) == 3
    assert not TaskStore(store).records(run_id)
    assert not store.unresolved_tool_calls(run_id)


def test_model_plan_cannot_escalate_permissions_or_exceed_budget(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "invalid.sqlite3")
    run_id = store.create_run(
        objective="Read",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    token = store.acquire_lease(run_id, "planner")
    with pytest.raises(PermissionError):
        save_model_plan(
            store,
            run_id,
            token,
            "escalate",
            {
                "tasks": [
                    {
                        "id": "edit",
                        "objective": "Edit",
                        "permissions": ["workspace.write"],
                        "dependencies": [],
                        "acceptance_criteria": ["Edited"],
                    }
                ]
            },
        )
    with pytest.raises(ValueError, match="budget"):
        save_model_plan(
            store,
            run_id,
            token,
            "budget",
            {
                "tasks": [
                    {
                        "id": "too-big",
                        "objective": "Read",
                        "max_iterations": 100,
                        "dependencies": [],
                        "acceptance_criteria": ["Read"],
                    }
                ]
            },
        )
    assert TaskStore(store).records(run_id) == []
    assert not store.unresolved_tool_calls(run_id)

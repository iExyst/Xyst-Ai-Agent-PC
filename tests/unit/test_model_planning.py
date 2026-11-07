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


async def test_malformed_plan_tool_batch_cannot_persist_partial_graph(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "plan-batch.sqlite3")
    target = workspace_tmp_path / "must-not-read.txt"
    target.write_text("do not touch", encoding="utf-8")
    run_id = store.create_run(
        objective="Save a plan and do not read files.",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_iterations=8,
        max_seconds=600,
    )["id"]
    valid = {
        "tasks": [
            {
                "id": "inspect",
                "objective": "Inspect the requested evidence.",
                "dependencies": [],
                "acceptance_criteria": ["Evidence recorded."],
                "max_iterations": 1,
                "max_seconds": 60,
            }
        ]
    }

    class BatchPlanner(LMStudioProvider):
        calls = 0

        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                assert TaskStore(store).records(run_id) == []
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "batch-plan-1",
                                        "type": "function",
                                        "function": {
                                            "name": "plan_save",
                                            "arguments": json.dumps(valid),
                                        },
                                    },
                                    {
                                        "id": "batch-plan-2",
                                        "type": "function",
                                        "function": {
                                            "name": "plan_save",
                                            "arguments": json.dumps(valid),
                                        },
                                    },
                                    {
                                        "id": "batch-read",
                                        "type": "function",
                                        "function": {
                                            "name": "workspace_read_file",
                                            "arguments": json.dumps({"path": target.name}),
                                        },
                                    },
                                ],
                            }
                        }
                    ]
                }
            if self.calls == 2:
                assert TaskStore(store).records(run_id) == []
                results = [
                    json.loads(message["content"])
                    for message in messages[-3:]
                    if message["role"] == "tool"
                ]
                assert [message["tool_call_id"] for message in messages[-3:]] == [
                    "batch-plan-1",
                    "batch-plan-2",
                    "batch-read",
                ]
                assert [result["code"] for result in results[:2]] == [
                    "plan_save_must_be_unique_final_call",
                    "plan_save_must_be_unique_final_call",
                ]
                assert all(
                    "exactly one plan_save call, as the final tool call" in result["message"]
                    for result in results[:2]
                )
                assert results[2]["error"]["code"] == "invalid_plan_tool_batch"
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "corrected-plan",
                                        "type": "function",
                                        "function": {
                                            "name": "plan_save",
                                            "arguments": json.dumps(valid),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            assert messages[-1]["tool_call_id"] == "corrected-plan"
            return {"choices": [{"message": {"role": "assistant", "content": "Plan saved."}}]}

    provider = BatchPlanner()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()

    assert provider.calls == 3
    assert store.get_run(run_id)["state"] == "verifying"
    assert TaskStore(store).readiness(run_id) == (["inspect"], [])
    assert target.read_text(encoding="utf-8") == "do not touch"
    assert store.unresolved_tool_calls(run_id) == []
    rejected_read_intent = next(
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "tool.intended"
        and event["payload"].get("call_id") == "batch-read"
    )
    assert rejected_read_intent["payload"]["tool"] == "model.plan_batch_rejected"
    assert not any(
        event["event_type"] == "tool.completed" and event["payload"].get("call_id") == "batch-read"
        for event in store.list_events(run_id)
    )
    assert [
        message["tool_call_id"]
        for message in store.conversation_messages(run_id)
        if message["role"] == "tool"
    ] == ["batch-plan-1", "batch-plan-2", "batch-read", "corrected-plan"]


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

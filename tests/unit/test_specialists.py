import asyncio
import json
import time

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.memory import MemoryStore
from xaa.storage.runs import RunStore
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.tasks import TaskStore


@pytest.mark.parametrize("threshold,output_limit", [(None, None), (20, 8)])
async def test_independent_specialists_read_scoped_files_without_polluting_parent_history(
    workspace_tmp_path,
    threshold,
    output_limit,
):
    store = RunStore(workspace_tmp_path / "specialists.sqlite3")
    for name in ("a", "b"):
        (workspace_tmp_path / f"{name}.txt").write_text(f"evidence-{name}")
    run_id = store.create_run(
        objective="Parent context must not be copied",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_total_tokens=threshold,
        max_output_tokens=output_limit,
    )["id"]
    parent = store.acquire_lease(run_id, "coordinator")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id=name,
                objective=name,
                max_iterations=2,
                max_total_tokens=10 if threshold is not None else None,
                max_output_tokens=4 if output_limit is not None else None,
            )
            for name in ("a", "b")
        ],
    )

    class Reader(LMStudioProvider):
        async def chat(self, *, model, messages, tools=None, max_output_tokens=None):
            assert max_output_tokens == (4 if output_limit is not None else None)
            assert "Parent context must not be copied" not in json.dumps(messages)
            assert {tool["function"]["name"] for tool in tools} == {
                "workspace_read_file",
                "workspace_list_directory",
            }
            await asyncio.sleep(0.01)
            if messages[-1]["role"] == "user":
                name = json.loads(messages[-1]["content"])["objective"]
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "same-id",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": json.dumps({"path": f"{name}.txt"}),
                            },
                        }
                    ],
                }
            else:
                result = json.loads(messages[-1]["content"])
                message = {"role": "assistant", "content": result["text"]}
            return {
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

    provider = Reader()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()
    assert statuses == [
        {"task_id": "a", "status": "verifying"},
        {"task_id": "b", "status": "verifying"},
    ]
    assert [json.loads(row["result"])["candidate"] for row in tasks.records(run_id)] == [
        "evidence-a",
        "evidence-b",
    ]
    assert store.conversation_messages(run_id) == []
    assert not store.unresolved_tool_calls(run_id)
    assert store.token_usage(run_id)["total_tokens"] == 20


async def test_specialist_write_request_fails_without_modifying_file(workspace_tmp_path):
    target = workspace_tmp_path / "target.txt"
    target.write_bytes(b"unchanged")
    store = RunStore(workspace_tmp_path / "denied.sqlite3")
    run_id = store.create_run(
        objective="Parent can write",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(id="read", objective="Read only", max_iterations=1),
            TaskDefinition(
                id="dependent", objective="Wait", dependencies=["read"], max_iterations=1
            ),
        ],
    )

    class Writer(LMStudioProvider):
        async def chat(self, **kwargs):
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "bad",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_write_file",
                                        "arguments": '{"path":"target.txt","content":"changed"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    provider = Writer()
    try:
        runner = SpecialistRunner(store, provider)
        await runner.run_ready(run_id, parent, deadline=time.monotonic() + 30)
        await runner.run_ready(run_id, parent, deadline=time.monotonic() + 30)
    finally:
        await provider.close()
    assert target.read_bytes() == b"unchanged"
    assert [row["status"] for row in tasks.records(run_id)] == ["blocked", "failed"]
    failure = json.loads(
        next(row for row in tasks.records(run_id) if row["local_id"] == "read")["result"]
    )
    assert failure["causes"] == ["PermissionError"]


async def test_shared_budget_is_charged_before_specialist_inference(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "budget.sqlite3")
    run_id = store.create_run(
        objective="Budget",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_iterations=1,
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="task", objective="Read", max_iterations=1)])
    store.append_worker_event(
        run_id,
        parent,
        "model.message",
        {"message": {"role": "assistant", "content": "Parent used the budget"}},
    )

    class NoCalls(LMStudioProvider):
        async def chat(self, **kwargs):
            raise AssertionError("No budget remains for specialist inference")

    provider = NoCalls()
    try:
        await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()
    assert tasks.records(run_id)[0]["status"] == "failed"
    assert not any(
        event["event_type"] == "task.inference.started" for event in store.list_events(run_id)
    )


async def test_coordinator_dispatches_saved_plan_and_receives_candidate_status(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "dispatch.sqlite3")
    run_id = store.create_run(
        objective="Execute the saved plan",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "planner")
    SpecialistProfileStore(store).set_instructions(
        "researcher", "Prefer primary sources and cite file paths."
    )
    MemoryStore(store).save_preference(
        "specialist:researcher", "Use the checked-in API contract as the source of truth."
    )
    TaskStore(store).save_plan(
        run_id,
        parent,
        [TaskDefinition(id="research", objective="Inspect", role="researcher", max_iterations=1)],
    )
    store.release_lease(run_id, parent)

    class DispatchProvider(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            if messages[0]["content"].startswith("You are the"):
                assert "Prefer primary sources and cite file paths." in messages[0]["content"]
                assert (
                    "Use the checked-in API contract as the source of truth."
                    in messages[1]["content"]
                )
                message = {"role": "assistant", "content": "Specialist candidate awaiting review"}
            elif messages[-1]["role"] == "user":
                assert any(tool["function"]["name"] == "plan_run_ready" for tool in tools)
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "dispatch",
                            "type": "function",
                            "function": {"name": "plan_run_ready", "arguments": "{}"},
                        }
                    ],
                }
            else:
                result = json.loads(messages[-1]["content"])
                assert result["tasks"] == [{"task_id": "research", "status": "verifying"}]
                message = {
                    "role": "assistant",
                    "content": "The specialist candidate awaits independent review.",
                }
            return {"choices": [{"message": message}]}

    provider = DispatchProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "verifying"
    assert TaskStore(store).records(run_id)[0]["status"] == "verifying"
    assert len(store.conversation_messages(run_id)) == 3
    assert not store.unresolved_tool_calls(run_id)

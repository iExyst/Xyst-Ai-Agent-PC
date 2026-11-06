import asyncio
import json
import time

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.providers.streaming import StreamAssemblyError
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.memory import MemoryStore
from xaa.storage.runs import RunStore
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.tasks import TaskStore


@pytest.mark.parametrize(
    "threshold,output_limit,pre_reserve",
    [(None, None, False), (20, 8, False), (100, 8, True)],
)
async def test_independent_specialists_read_scoped_files_without_polluting_parent_history(
    workspace_tmp_path,
    threshold,
    output_limit,
    pre_reserve,
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
        pre_inference_token_reservations=pre_reserve,
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
                max_total_tokens=10 if threshold is not None and not pre_reserve else None,
                max_output_tokens=4 if output_limit is not None else None,
            )
            for name in ("a", "b")
        ],
    )

    class Reader(LMStudioProvider):
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
            if pre_reserve:
                assert require_exact_context is True and on_admission is not None
                on_admission(3, max_output_tokens)
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
    if pre_reserve:
        report = store.usage_report(run_id)
        assert report["outstanding_reservations"] == 0
        assert (
            len(
                [
                    event
                    for event in store.list_events(run_id)
                    if event["event_type"] == "tokens.reserved"
                ]
            )
            == 4
        )


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


async def test_enabled_specialist_stream_is_task_scoped_and_saved_before_final_message(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "specialist-stream.sqlite3")
    run_id = store.create_run(
        objective="Parent objective stays private to its specialist",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="research", objective="Inspect evidence", max_iterations=1)],
    )
    draft = "specialist evidence " * 20

    class StreamingResearcher(LMStudioProvider):
        async def chat_streamed(self, *, on_content, **kwargs):
            assert "Parent objective stays private" not in json.dumps(kwargs["messages"])
            on_content(draft)
            return {
                "choices": [{"message": {"role": "assistant", "content": draft}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            }

        async def chat(self, **kwargs):
            raise AssertionError(
                "Streaming specialist inference must use the streaming provider path"
            )

    provider = StreamingResearcher()
    try:
        result = await SpecialistRunner(store, provider, stream_responses=True).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()

    assert result == [{"task_id": "research", "status": "verifying"}]
    events = store.public_events(run_id)
    deltas = [event for event in events if event["event_type"] == "task.model.delta"]
    final = next(event for event in events if event["event_type"] == "task.model.message")
    outcome = next(event for event in events if event["event_type"] == "task.result")
    assert "".join(event["payload"]["text"] for event in deltas) == draft
    assert all(len(event["payload"]["text"]) <= 128 for event in deltas)
    assert all(event["payload"]["task_id"] == "research" for event in deltas)
    assert all(set(event["payload"]) == {"task_id", "inference_id", "text"} for event in deltas)
    assert deltas[0]["sequence"] < final["sequence"] < outcome["sequence"]
    assert json.loads(tasks.records(run_id)[0]["result"])["candidate"] == draft


async def test_specialist_retries_one_invalid_stream_within_its_saved_budget(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-stream-recovery.sqlite3")
    run_id = store.create_run(
        objective="Parent objective stays private to its specialist",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="research", objective="Inspect evidence", max_iterations=2)],
    )

    class RecoveringResearcher(LMStudioProvider):
        calls = 0

        async def chat_streamed(self, *, on_content, **kwargs):
            self.calls += 1
            if self.calls == 1:
                on_content("Unverified partial draft")
                raise StreamAssemblyError("invalid stream", code="invalid_or_oversized_chunk")
            assert "No part of it was executed" in kwargs["messages"][-1]["content"]
            on_content("Recovered candidate")
            return {
                "choices": [{"message": {"role": "assistant", "content": "Recovered candidate"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

    provider = RecoveringResearcher()
    try:
        result = await SpecialistRunner(store, provider, stream_responses=True).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()

    assert result == [{"task_id": "research", "status": "verifying"}]
    events = store.list_events(run_id)
    rejected = [event for event in events if event["event_type"] == "task.model.stream_rejected"]
    requested = [event for event in events if event["event_type"] == "task.response_requested"]
    assert len([event for event in events if event["event_type"] == "task.inference.started"]) == 2
    assert len(rejected) == 1
    assert rejected[0]["payload"]["code"] == "invalid_or_oversized_chunk"
    assert len(requested) == 1
    assert json.loads(tasks.records(run_id)[0]["result"]) == {"candidate": "Recovered candidate"}
    assert not any(event["event_type"] == "tool.intended" for event in events)


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
    SpecialistProfileStore(store).set_display_name("researcher", "Source Analyst")
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

        async def chat_streamed(self, *, model, messages, tools=None, on_content=None):
            response = await self.chat(model=model, messages=messages, tools=tools)
            content = response["choices"][0]["message"].get("content")
            if content and on_content is not None:
                on_content(content)
            return response

        async def chat(self, *, model, messages, tools=None):
            if messages[0]["content"].startswith("You are "):
                assert messages[0]["content"].startswith(
                    "You are Source Analyst (researcher specialist)."
                )
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
        await Coordinator(store, provider, stream_responses=True).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "verifying"
    assert TaskStore(store).records(run_id)[0]["status"] == "verifying"
    assert len(store.conversation_messages(run_id)) == 3
    assert not store.unresolved_tool_calls(run_id)
    specialist_deltas = [
        event for event in store.list_events(run_id) if event["event_type"] == "task.model.delta"
    ]
    assert "".join(event["payload"]["text"] for event in specialist_deltas) == (
        "Specialist candidate awaiting review"
    )

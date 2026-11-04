import json

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


class ScriptedProvider(LMStudioProvider):
    async def diagnose_tools(self, model, *, request=None):
        return True

    async def chat(self, *, model, messages, tools=None):
        if messages[-1]["role"] == "user":
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "read-1",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_read_file",
                                        "arguments": '{"path":"input.txt"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        assert messages[-1]["tool_call_id"] == "read-1"
        assert "fixture evidence" in messages[-1]["content"]
        return {"choices": [{"message": {"role": "assistant", "content": "Candidate result"}}]}


class EditingProvider(ScriptedProvider):
    async def chat(self, *, model, messages, tools=None):
        if messages[-1]["role"] == "user":
            return await super().chat(model=model, messages=messages, tools=tools)
        result = json.loads(messages[-1]["content"])
        if "bytes_written" in result:
            return {"choices": [{"message": {"role": "assistant", "content": "Edited input.txt"}}]}
        assert any(tool["function"]["name"] == "workspace_write_file" for tool in tools)
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "edit-1",
                                "type": "function",
                                "function": {
                                    "name": "workspace_write_file",
                                    "arguments": json.dumps(
                                        {
                                            "path": "input.txt",
                                            "content": "changed",
                                            "expected_sha256": result["sha256"],
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        }


async def test_workspace_coordinator_reads_then_edits_with_returned_hash(workspace_tmp_path):
    target = workspace_tmp_path / "input.txt"
    target.write_text("fixture evidence", encoding="utf-8")
    store = RunStore(workspace_tmp_path / "edit.sqlite3")
    run_id = store.create_run(
        objective="Edit",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
    )["id"]
    provider = EditingProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    assert target.read_text() == "changed"
    assert store.get_run(run_id)["state"] == "verifying"
    assert not store.unresolved_tool_calls(run_id)


async def test_coordinator_executes_journaled_read_and_stops_for_verification(workspace_tmp_path):
    (workspace_tmp_path / "input.txt").write_text("fixture evidence", encoding="utf-8")
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Read input.txt",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    provider = ScriptedProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    run = store.get_run(run_id)
    assert run["state"] == "verifying"
    assert run["lease_owner"] is None
    assert store.unresolved_tool_calls(run_id) == []
    assert (
        len(
            [event for event in store.list_events(run_id) if event["event_type"] == "model.message"]
        )
        == 2
    )


async def test_recovery_blocks_unconfirmed_tool_request(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "recovery.sqlite3")
    run_id = store.create_run(
        objective="Read",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    token = store.acquire_lease(run_id, "interrupted-worker")
    assert token
    store.append_worker_event(
        run_id,
        token,
        "model.message",
        {
            "iteration": 0,
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "uncertain-call",
                        "type": "function",
                        "function": {
                            "name": "workspace_read_file",
                            "arguments": '{"path":"input.txt"}',
                        },
                    }
                ],
            },
        },
    )
    store.release_lease(run_id, token)
    provider = ScriptedProvider()
    try:
        await Coordinator(RunStore(store.path), provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "blocked"
    changes = [
        event for event in store.list_events(run_id) if event["event_type"] == "run.state_changed"
    ]
    assert changes[-1]["payload"]["reason"] == "incomplete_tool_sequence"


async def test_saved_iteration_budget_stops_before_second_model_turn(workspace_tmp_path):
    (workspace_tmp_path / "input.txt").write_text("fixture evidence", encoding="utf-8")
    store = RunStore(workspace_tmp_path / "budget.sqlite3")
    run_id = store.create_run(
        objective="Read input",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_iterations=1,
    )["id"]
    provider = ScriptedProvider()
    try:
        await Coordinator(RunStore(store.path), provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "blocked"
    assert (
        len(
            [event for event in store.list_events(run_id) if event["event_type"] == "model.message"]
        )
        == 1
    )

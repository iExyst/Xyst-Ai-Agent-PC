import json

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import LeaseLost, RunStore


def setup(root):
    store = RunStore(root / "read.sqlite3")
    run = store.create_run(
        objective="Read input.txt",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model="fixture",
    )
    return store, run["id"], store.acquire_lease(run["id"], "old-worker")


def propose(store, run_id, token, identity, name="workspace_read_file"):
    store.append_worker_event(
        run_id,
        token,
        "model.message",
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": identity,
                        "type": "function",
                        "function": {"name": name, "arguments": '{"path":"input.txt"}'},
                    }
                ],
            }
        },
    )


@pytest.mark.parametrize(
    "tool,function",
    [
        ("workspace.read_file", "workspace_read_file"),
        ("workspace.list_directory", "workspace_list_directory"),
        ("workspace.search", "workspace_search"),
    ],
)
def test_interrupted_read_has_a_failed_result_without_reexecuting(
    workspace_tmp_path, tool, function
):
    store, run_id, token = setup(workspace_tmp_path)
    propose(store, run_id, token, "read", function)
    store.record_tool_intent(
        run_id, token, call_id="read", tool=tool, version="1", arguments={}, idempotency_key="read"
    )
    assert store.resolve_interrupted_reads(run_id, token) == ["read"]
    assert store.resolve_interrupted_reads(run_id, token) == []
    messages = store.conversation_messages(run_id)
    failure = json.loads(messages[-1]["content"])
    assert failure["effect"] == "read_only" and failure["error"]["code"] == "interrupted_read"
    assert not store.unresolved_tool_calls(run_id)


def test_recovery_preserves_internal_results_and_unknown_effects(workspace_tmp_path):
    store, run_id, token = setup(workspace_tmp_path)
    propose(store, run_id, token, "private")
    for identity, tool, version in [
        ("private", "workspace.read_file", "1"),
        ("helper", "workspace.list_directory", "1"),
        ("write", "workspace.write_file", "1"),
        ("new-version", "workspace.read_file", "2"),
        ("unknown", "extension.read", "1"),
        ("executing", "workspace.read_file", "1"),
    ]:
        store.record_tool_intent(
            run_id,
            token,
            call_id=identity,
            tool=tool,
            version=version,
            arguments={},
            idempotency_key=identity,
            internal=identity == "private",
        )
    store.append_worker_event(run_id, token, "tool.executing", {"call_id": "executing"})
    assert set(store.resolve_interrupted_reads(run_id, token)) == {"private", "helper"}
    assert not [
        message for message in store.conversation_messages(run_id) if message["role"] == "tool"
    ]
    assert {call["id"] for call in store.unresolved_tool_calls(run_id)} == {
        "write",
        "new-version",
        "unknown",
        "executing",
    }


def test_read_recovery_requires_current_lease(workspace_tmp_path):
    store, run_id, old = setup(workspace_tmp_path)
    store.record_tool_intent(
        run_id,
        old,
        call_id="read",
        tool="workspace.read_file",
        version="1",
        arguments={},
        idempotency_key="read",
    )
    store.release_lease(run_id, old)
    fresh = store.acquire_lease(run_id, "new-worker")
    with pytest.raises(LeaseLost):
        store.resolve_interrupted_reads(run_id, old)
    assert store.resolve_interrupted_reads(run_id, fresh) == ["read"]


async def test_coordinator_can_request_fresh_read_after_interruption(workspace_tmp_path):
    (workspace_tmp_path / "input.txt").write_bytes(b"fresh evidence")
    store, run_id, token = setup(workspace_tmp_path)
    propose(store, run_id, token, "interrupted")
    store.record_tool_intent(
        run_id,
        token,
        call_id="interrupted",
        tool="workspace.read_file",
        version="1",
        arguments={"path": "input.txt"},
        idempotency_key="interrupted",
    )
    store.release_lease(run_id, token)

    class Reader(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            result = json.loads(messages[-1]["content"])
            if messages[-1]["tool_call_id"] == "interrupted":
                assert result["effect"] == "read_only"
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "fresh",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"input.txt"}',
                            },
                        }
                    ],
                }
            else:
                assert result["text"] == "fresh evidence"
                message = {"role": "assistant", "content": result["text"]}
            return {"choices": [{"message": message}]}

    provider = Reader()
    try:
        await Coordinator(RunStore(store.path), provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "verifying"
    assert not store.unresolved_tool_calls(run_id)
    assert [
        m["tool_call_id"] for m in store.conversation_messages(run_id) if m["role"] == "tool"
    ] == ["interrupted", "fresh"]

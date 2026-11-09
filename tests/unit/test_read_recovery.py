import json
import sqlite3
from uuid import uuid4

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
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


def propose(
    store, run_id, token, identity, name="workspace_read_file", arguments='{"path":"input.txt"}'
):
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
                        "function": {"name": name, "arguments": arguments},
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
        ("procedure.read_resource", "procedure_read_resource"),
        ("procedure.view", "procedure_view"),
        ("procedure.catalog", "procedure_catalog"),
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


@pytest.mark.parametrize(
    "tool,function",
    [
        ("procedure.read_resource", "procedure_read_resource"),
        ("procedure.view", "procedure_view"),
        ("procedure.catalog", "procedure_catalog"),
    ],
)
def test_abandoned_local_read_requeues_and_is_sealed(workspace_tmp_path, tool, function):
    store, run_id, token = setup(workspace_tmp_path)
    propose(store, run_id, token, "read", function)
    store.record_tool_intent(
        run_id,
        token,
        call_id="read",
        tool=tool,
        version="1",
        arguments={},
        idempotency_key="read",
    )
    store.append_worker_event(run_id, token, "tool.executing", {"call_id": "read"})
    store.transition(run_id, RunState.PLANNING, owner=token)
    connection = sqlite3.connect(store.path)
    try:
        with connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (run_id,),
            )
    finally:
        connection.close()

    assert store.recover_abandoned_runs() == [run_id]
    fresh = store.acquire_lease(run_id, "recovery-worker")
    assert fresh is not None
    assert store.resolve_interrupted_reads(run_id, fresh) == ["read"]
    assert not store.unresolved_tool_calls(run_id)
    messages = store.conversation_messages(run_id)
    result = json.loads(messages[-1]["content"])
    assert result["effect"] == "read_only"
    assert result["error"]["code"] == "interrupted_read"


@pytest.mark.parametrize("registered", [False, True])
def test_interrupted_git_read_discards_only_its_owned_artifact(workspace_tmp_path, registered):
    store, run_id, token = setup(workspace_tmp_path)
    call_id = "git-call"
    artifact_name = f"{uuid4()}.git-output.txt"
    artifact_path = store.path.parent / "artifacts" / artifact_name
    artifact_path.parent.mkdir(exist_ok=True)
    artifact_path.write_bytes(b"partial or complete Git output")
    propose(
        store,
        run_id,
        token,
        call_id,
        "workspace_git_inspect",
        json.dumps({"operation": "status"}),
    )
    store.record_tool_intent(
        run_id,
        token,
        call_id=call_id,
        tool="git.status",
        version="2",
        arguments={"workspace": str(workspace_tmp_path), "artifact_filename": artifact_name},
        idempotency_key=call_id,
    )
    if registered:
        store.register_artifact(run_id, token, artifact_name, provenance=f"git.status:{call_id}")
    store.append_worker_event(run_id, token, "tool.executing", {"call_id": call_id})
    store.transition(run_id, RunState.PLANNING, owner=token)
    connection = sqlite3.connect(store.path)
    try:
        with connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (run_id,),
            )
    finally:
        connection.close()

    assert store.recover_abandoned_runs() == [run_id]
    fresh = store.acquire_lease(run_id, "recovery-worker")
    assert fresh is not None
    assert store.resolve_interrupted_reads(run_id, fresh) == [call_id]
    assert not artifact_path.exists()
    assert store.list_artifacts(run_id) == []
    assert not store.unresolved_tool_calls(run_id)
    messages = store.conversation_messages(run_id)
    result = json.loads(messages[-1]["content"])
    assert result["effect"] == "read_only"
    assert result["error"]["code"] == "interrupted_read"
    discarded = [
        event for event in store.list_events(run_id) if event["event_type"] == "artifact.discarded"
    ]
    assert bool(discarded) is registered


def test_legacy_git_intent_stays_blocked_without_an_artifact_identity(workspace_tmp_path):
    store, run_id, token = setup(workspace_tmp_path)
    store.record_tool_intent(
        run_id,
        token,
        call_id="legacy-git",
        tool="git.status",
        version="1",
        arguments={"workspace": str(workspace_tmp_path)},
        idempotency_key="legacy-git",
    )
    store.transition(run_id, RunState.PLANNING, owner=token)
    connection = sqlite3.connect(store.path)
    try:
        with connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (run_id,),
            )
    finally:
        connection.close()

    assert store.recover_abandoned_runs() == []
    assert store.get_run(run_id)["state"] == RunState.BLOCKED
    assert {call["id"] for call in store.unresolved_tool_calls(run_id)} == {"legacy-git"}


@pytest.mark.parametrize("permission,recoverable", [("read", True), ("write", False)])
def test_parent_mcp_recovery_uses_saved_user_permission(
    workspace_tmp_path, permission, recoverable
):
    store, run_id, token = setup(workspace_tmp_path)
    qualified = f"mcp_{'a' * 32}_fixture"
    call_id = "mcp-call"
    propose(store, run_id, token, call_id, qualified, "{}")
    store.record_tool_intent(
        run_id,
        token,
        call_id=call_id,
        tool="mcp.call",
        version="2",
        arguments={"tool": qualified, "arguments": {}, "permission": permission},
        idempotency_key=call_id,
    )
    store.append_worker_event(run_id, token, "tool.executing", {"call_id": call_id})
    store.transition(run_id, RunState.PLANNING, owner=token)
    connection = sqlite3.connect(store.path)
    try:
        with connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (run_id,),
            )
    finally:
        connection.close()

    recovered = store.recover_abandoned_runs()
    if not recoverable:
        assert recovered == []
        assert store.get_run(run_id)["state"] == RunState.BLOCKED
        assert {call["id"] for call in store.unresolved_tool_calls(run_id)} == {call_id}
        return

    assert recovered == [run_id]
    fresh = store.acquire_lease(run_id, "recovery-worker")
    assert fresh is not None
    assert store.resolve_interrupted_reads(run_id, fresh) == [call_id]
    assert not store.unresolved_tool_calls(run_id)
    result = json.loads(store.conversation_messages(run_id)[-1]["content"])
    assert result["effect"] == "read_only"
    assert result["error"]["code"] == "interrupted_read"


def test_recovery_preserves_internal_results_and_unknown_effects(workspace_tmp_path):
    (workspace_tmp_path / "input.txt").write_bytes(b"unchanged")
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
    assert set(store.resolve_interrupted_reads(run_id, token)) == {
        "private",
        "helper",
        "executing",
    }
    assert not [
        message for message in store.conversation_messages(run_id) if message["role"] == "tool"
    ]
    failures = [
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "tool.failed" and event["payload"]["call_id"] == "executing"
    ]
    assert failures[0]["payload"]["code"] == "interrupted_read"
    assert failures[0]["payload"]["effect"] == "read_only"
    assert (workspace_tmp_path / "input.txt").read_bytes() == b"unchanged"
    assert {call["id"] for call in store.unresolved_tool_calls(run_id)} == {
        "write",
        "new-version",
        "unknown",
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

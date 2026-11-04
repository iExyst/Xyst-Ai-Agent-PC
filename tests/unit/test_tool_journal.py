import pytest
from xaa.storage.runs import RunStore


def test_interrupted_action_requires_reconciliation(workspace_tmp_path):
    path = workspace_tmp_path / "journal.sqlite3"
    store = RunStore(path)
    run_id = store.create_run(
        objective="Task", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    assert token
    action = {
        "call_id": "call-1",
        "tool": "write_file",
        "version": "1",
        "arguments": {"path": "a.txt"},
        "idempotency_key": "action-1",
    }
    assert store.record_tool_intent(run_id, token, **action)
    recovered = RunStore(path)
    assert len(recovered.unresolved_tool_calls(run_id)) == 1
    assert not recovered.record_tool_intent(run_id, token, **action)
    with pytest.raises(ValueError, match="different action"):
        recovered.record_tool_intent(run_id, token, **{**action, "arguments": {"path": "b.txt"}})
    recovered.record_tool_result(run_id, token, "call-1", {"verified_destination": True})
    assert recovered.unresolved_tool_calls(run_id) == []
    assert recovered.list_events(run_id)[-1]["event_type"] == "tool.completed"


def test_provider_call_ids_can_repeat_in_separate_runs(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "shared.sqlite3")
    identities = []
    for content in ("first", "second"):
        run_id = store.create_run(
            objective=content, acceptance_criteria=[], workspace=None, mode="observe", model=None
        )["id"]
        token = store.acquire_lease(run_id, "worker")
        assert store.record_tool_intent(
            run_id,
            token,
            call_id="call-0",
            tool="read",
            version="1",
            arguments={},
            idempotency_key="action-0",
        )
        store.record_tool_result(run_id, token, "call-0", {"content": content})
        with pytest.raises(ValueError, match="Tool call ID"):
            store.record_tool_intent(
                run_id,
                token,
                call_id="call-0",
                tool="read",
                version="1",
                arguments={},
                idempotency_key="another-action",
            )
        identities.append(run_id)
    reopened = RunStore(store.path)
    for run_id, content in zip(identities, ("first", "second"), strict=True):
        messages = reopened.conversation_messages(run_id)
        assert messages[0]["tool_call_id"] == "call-0"
        assert content in messages[0]["content"]

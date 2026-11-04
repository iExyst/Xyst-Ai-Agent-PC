import pytest
from xaa.runtime.executor import WorkspaceExecutor
from xaa.storage.runs import RunStore


@pytest.mark.parametrize("call_id", ["verification-model-generated", "model-generated:source"])
def test_model_tool_ids_do_not_get_filtered_as_internal(workspace_tmp_path, call_id):
    (workspace_tmp_path / "file.txt").write_bytes(b"data")
    store = RunStore(workspace_tmp_path / "db.sqlite3")
    run_id = store.create_run(
        objective="Read",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    store.append_worker_event(
        run_id,
        token,
        "model.message",
        {"message": {"role": "assistant", "tool_calls": [{"id": call_id}]}},
    )
    WorkspaceExecutor(store).read_file(
        run_id, token, call_id, {"path": "file.txt"}, idempotency_key=call_id
    )
    messages = store.conversation_messages(run_id)
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == call_id

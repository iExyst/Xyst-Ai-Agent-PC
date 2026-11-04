import hashlib

import pytest
from xaa.runtime.executor import ToolPreconditionError, WorkspaceExecutor
from xaa.storage.runs import RunStore


def test_targeted_edit_preserves_surroundings_and_refuses_ambiguous_match(workspace_tmp_path):
    target = workspace_tmp_path / "config.txt"
    original = b"# preserve\r\nvalue=1\r\n"
    target.write_bytes(original)
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Edit",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    executor = WorkspaceExecutor(store)
    executor.replace_text(
        run_id,
        token,
        "edit",
        {
            "path": "config.txt",
            "old_text": "value=1",
            "new_text": "value=2",
            "expected_sha256": hashlib.sha256(original).hexdigest(),
        },
        idempotency_key="edit",
    )
    assert target.read_bytes() == b"# preserve\r\nvalue=2\r\n"
    with pytest.raises(ValueError, match="exactly one"):
        executor.replace_text(
            run_id,
            token,
            "ambiguous",
            {
                "path": "config.txt",
                "old_text": "preserve-not-present",
                "new_text": "x",
                "expected_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
            idempotency_key="ambiguous",
        )


@pytest.mark.parametrize(
    ("original", "old", "new", "stale", "code"),
    [
        (b"one", "one", "two", True, "file_changed"),
        (b"one one", "one", "two", False, "match_count"),
        (b"\xff", "x", "y", False, "invalid_encoding"),
        (b"x", "x", "\u2713" * 22000, False, "output_too_large"),
    ],
    ids=["stale", "ambiguous", "invalid-utf8", "byte-limit"],
)
def test_replacement_preconditions_are_durable_known_failures(
    workspace_tmp_path, original, old, new, stale, code
):
    target = workspace_tmp_path / "file.txt"
    target.write_bytes(original)
    store = RunStore(workspace_tmp_path / "db.sqlite3")
    run_id = store.create_run(
        objective="Edit",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    with pytest.raises(ToolPreconditionError) as caught:
        WorkspaceExecutor(store).replace_text(
            run_id,
            token,
            "replace",
            {
                "path": "file.txt",
                "old_text": old,
                "new_text": new,
                "expected_sha256": "0" * 64 if stale else hashlib.sha256(original).hexdigest(),
            },
            idempotency_key="replace",
        )
    assert caught.value.result["effect"] == "not_started"
    assert caught.value.result["error"]["code"] == code
    assert target.read_bytes() == original
    assert store.unresolved_tool_calls(run_id) == []
    events = store.list_events(run_id)
    assert any(event["event_type"] == "tool.failed" for event in events)
    assert not any(event["event_type"] == "tool.executing" for event in events)

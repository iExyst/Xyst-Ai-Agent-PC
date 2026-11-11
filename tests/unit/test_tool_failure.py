import hashlib
import json

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.executor import WorkspaceExecutor
from xaa.storage.runs import RunStore


@pytest.mark.parametrize("replacement", [False, True], ids=["write", "replace"])
async def test_model_can_reread_and_correct_known_prewrite_failure(workspace_tmp_path, replacement):
    target = workspace_tmp_path / "file.txt"
    target.write_bytes(b"before")
    store = RunStore(workspace_tmp_path / "failure.sqlite3")
    run_id = store.create_run(
        objective="Edit safely",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
        file_checks=[{"path": "file.txt", "expected_content": "after"}],
    )["id"]

    class CorrectingWriter(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            if messages[-1]["role"] == "user":
                name, identity, args = (
                    "workspace_replace_text" if replacement else "workspace_write_file",
                    "stale",
                    {
                        "path": "file.txt",
                        "expected_sha256": "0" * 64,
                        **(
                            {"old_text": "before", "new_text": "after"}
                            if replacement
                            else {"content": "after"}
                        ),
                    },
                )
            elif messages[-1]["tool_call_id"] == "stale":
                result = json.loads(messages[-1]["content"])
                assert result["effect"] == "not_started"
                assert result["error"]["code"] == (
                    "file_changed" if replacement else "read_required"
                )
                assert target.read_bytes() == b"before"
                name, identity, args = "workspace_read_file", "reread", {"path": "file.txt"}
            elif messages[-1]["tool_call_id"] == "reread":
                result = json.loads(messages[-1]["content"])
                name, identity, args = (
                    "workspace_replace_text" if replacement else "workspace_write_file",
                    "corrected",
                    {
                        "path": "file.txt",
                        "expected_sha256": result["sha256"],
                        **(
                            {"old_text": "before", "new_text": "after"}
                            if replacement
                            else {"content": "after"}
                        ),
                    },
                )
            else:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Corrected file; review pending.",
                            }
                        }
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
                                    "function": {"name": name, "arguments": json.dumps(args)},
                                }
                            ],
                        }
                    }
                ]
            }

    provider = CorrectingWriter()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    reopened = RunStore(store.path)
    assert target.read_bytes() == b"after"
    assert reopened.get_run(run_id)["state"] == "verifying"
    assert not reopened.unresolved_tool_calls(run_id)
    assert [
        message["tool_call_id"]
        for message in reopened.conversation_messages(run_id)
        if message["role"] == "tool"
    ] == ["stale", "reread", "corrected"]
    assert (
        len(
            [
                event
                for event in reopened.list_events(run_id)
                if event["event_type"] == "tool.failed"
            ]
        )
        == 1
    )


def test_error_after_write_started_remains_uncertain(workspace_tmp_path, monkeypatch):
    target = workspace_tmp_path / "file.txt"
    target.write_bytes(b"before")
    store = RunStore(workspace_tmp_path / "uncertain.sqlite3")
    run_id = store.create_run(
        objective="Edit",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    executor = WorkspaceExecutor(store)
    executor.read_file(run_id, token, "read", {"path": "file.txt"}, idempotency_key="read")

    def failed_flush(descriptor):
        raise OSError("Injected fsync failure after write")

    monkeypatch.setattr("xaa.runtime.executor.os.fsync", failed_flush)
    with pytest.raises(OSError, match="fsync"):
        executor.write_file(
            run_id,
            token,
            "write",
            {
                "path": "file.txt",
                "content": "after",
                "expected_sha256": hashlib.sha256(b"before").hexdigest(),
            },
            idempotency_key="write",
        )
    assert target.read_bytes() == b"after"
    assert store.unresolved_tool_calls(run_id)[0]["id"] == "write"
    with pytest.raises(ValueError, match="reconciliation"):
        store.record_tool_failure(
            run_id, token, "write", code="incorrect", message="Must not resolve uncertainty."
        )
    assert not any(event["event_type"] == "tool.failed" for event in store.list_events(run_id))

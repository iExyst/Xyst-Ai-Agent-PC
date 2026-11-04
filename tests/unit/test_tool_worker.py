import asyncio
import sys

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.tool_worker import (
    FilesystemWorkerError,
    FilesystemWorkerTimeout,
    execute_filesystem,
)
from xaa.storage.runs import RunStore


def owned_run(root):
    store = RunStore(root / "db.sqlite3")
    run = store.create_run(
        objective="Worker",
        acceptance_criteria=[],
        workspace=str(root),
        mode="workspace",
        model="fixture",
    )
    return store, run["id"], store.acquire_lease(run["id"], "worker")


async def test_expired_budget_and_oversized_request_do_not_spawn(workspace_tmp_path, monkeypatch):
    store, run_id, token = owned_run(workspace_tmp_path)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Worker must not spawn.")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(FilesystemWorkerTimeout):
        await execute_filesystem(
            store,
            run_id,
            token,
            "expired",
            "read",
            {"path": "f"},
            idempotency_key="expired",
            timeout=0,
        )
    with pytest.raises(FilesystemWorkerError, match="request exceeded"):
        await execute_filesystem(
            store,
            run_id,
            token,
            "large",
            "write",
            {"content": "x" * 1_048_576},
            idempotency_key="large",
        )


async def test_real_worker_journals_create_read_and_collision(workspace_tmp_path):
    store, run_id, token = owned_run(workspace_tmp_path)
    result = await execute_filesystem(
        store,
        run_id,
        token,
        "create",
        "create",
        {"path": "new.txt", "content": "worker evidence"},
        idempotency_key="create",
    )
    assert result["created"]
    read = await execute_filesystem(
        store, run_id, token, "read", "read", {"path": "new.txt"}, idempotency_key="read"
    )
    assert read["text"] == "worker evidence"
    collision = await execute_filesystem(
        store,
        run_id,
        token,
        "collision",
        "create",
        {"path": "new.txt", "content": "overwrite"},
        idempotency_key="collision",
    )
    assert collision["effect"] == "not_started"
    assert (workspace_tmp_path / "new.txt").read_bytes() == b"worker evidence"
    assert not store.unresolved_tool_calls(run_id)


async def test_real_worker_list_search_and_internal_reads(workspace_tmp_path):
    store, run_id, token = owned_run(workspace_tmp_path)
    (workspace_tmp_path / "marker.txt").write_bytes(b"unique worker marker\n")
    listing = await execute_filesystem(
        store, run_id, token, "list", "list", {"path": "."}, idempotency_key="list", internal=True
    )
    assert any(entry["name"] == "marker.txt" for entry in listing["entries"])
    read = await execute_filesystem(
        store,
        run_id,
        token,
        "private-read",
        "read",
        {"path": "marker.txt"},
        idempotency_key="private-read",
        internal=True,
    )
    assert read["text"] == "unique worker marker\n"
    found = await execute_filesystem(
        store,
        run_id,
        token,
        "search",
        "search",
        {"query": "unique worker marker"},
        idempotency_key="search",
    )
    assert any(match["path"] == "marker.txt" and match["line"] == 1 for match in found["matches"])
    assert not store.unresolved_tool_calls(run_id)
    replay = store.conversation_messages(run_id)
    assert [message["tool_call_id"] for message in replay if message["role"] == "tool"] == [
        "search"
    ]


@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancel"])
async def test_blocked_worker_is_killed_and_joined_without_blocking_loop(
    workspace_tmp_path, monkeypatch, cancel
):
    store, run_id, token = owned_run(workspace_tmp_path)
    original = asyncio.create_subprocess_exec
    started = asyncio.Event()
    processes = []

    async def spawn(*command, **kwargs):
        if command[0] == sys.executable:
            process = await original(
                sys.executable,
                "-c",
                "import sys,time; sys.stdin.buffer.read(); time.sleep(60)",
                **kwargs,
            )
            processes.append(process)
            started.set()
            return process
        return await original(*command, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        execute_filesystem(
            store,
            run_id,
            token,
            "read",
            "read",
            {"path": "never"},
            idempotency_key="read",
            timeout=1 if cancel else 0.15,
        )
    )
    await asyncio.wait_for(started.wait(), 2)
    assert processes[0].returncode is None
    await asyncio.sleep(0.02)  # The event loop remains responsive while the worker is blocked.
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert processes[0].returncode is not None
    assert store.unresolved_tool_calls(run_id) == []


async def test_coordinator_blocks_on_worker_failure(workspace_tmp_path):
    store, run_id, token = owned_run(workspace_tmp_path)
    store.release_lease(run_id, token)

    class MissingFileProvider(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "missing",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_read_file",
                                        "arguments": '{"path":"absent"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    provider = MissingFileProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "blocked"
    transitions = [
        event for event in store.list_events(run_id) if event["event_type"] == "run.state_changed"
    ]
    assert transitions[-1]["payload"]["reason"] == "filesystem_worker_interrupted"


async def test_unicode_worker_protocol_ignores_windows_text_encoding(
    workspace_tmp_path, monkeypatch
):
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252:strict")
    monkeypatch.setenv("PYTHONUTF8", "0")
    store, run_id, token = owned_run(workspace_tmp_path)
    filename = "\u8cc7\u6599.txt"
    content = "R\u00e9sum\u00e9 \u3053\u3093\u306b\u3061\u306f\n"
    await execute_filesystem(
        store,
        run_id,
        token,
        "unicode-create",
        "create",
        {"path": filename, "content": content},
        idempotency_key="unicode-create",
    )
    result = await execute_filesystem(
        store,
        run_id,
        token,
        "unicode-read",
        "read",
        {"path": filename},
        idempotency_key="unicode-read",
    )
    assert result["text"] == content
    assert (workspace_tmp_path / filename).read_bytes() == content.encode("utf-8")
    assert not store.unresolved_tool_calls(run_id)

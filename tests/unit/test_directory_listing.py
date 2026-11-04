import json
import os
import subprocess

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.directory_listing import list_directory
from xaa.storage.runs import RunStore


def make_run(root):
    store = RunStore(root / "runs.sqlite3")
    run_id = store.create_run(
        objective="Discover files",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model="fixture",
    )["id"]
    return store, run_id


def test_real_directory_listing_is_bounded_and_rejects_escape(workspace_tmp_path):
    root = workspace_tmp_path
    (root / "子目录").mkdir()
    (root / "unique.txt").write_text("content")
    store, run_id = make_run(root)
    token = store.acquire_lease(run_id, "worker")
    result = list_directory(store, run_id, token, "list", {"path": "."})
    assert {"name": "子目录", "kind": "directory"} in result["entries"]
    assert {"name": "unique.txt", "kind": "file"} in result["entries"]
    assert result["trust"] == "untrusted_workspace_content"
    assert not store.unresolved_tool_calls(run_id)
    for path in ["..", str(root.parent)]:
        with pytest.raises(PermissionError):
            list_directory(store, run_id, token, "escape", {"path": path})
    large = root / "large"
    large.mkdir()
    for index in range(300):
        (large / f"{index}.txt").touch()
    result = list_directory(store, run_id, token, "bounded", {"path": "large"})
    assert result["truncated"] is True
    assert len(result["entries"]) == 256


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary check")
def test_listing_does_not_follow_junction_outside_workspace(workspace_tmp_path):
    root = workspace_tmp_path / "workspace"
    outside = workspace_tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "private.txt").touch()
    junction = root / "external"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True
    )
    try:
        store, run_id = make_run(root)
        token = store.acquire_lease(run_id, "worker")
        result = list_directory(store, run_id, token, "root", {"path": "."})
        assert {"name": "external", "kind": "link"} in result["entries"]
        with pytest.raises(PermissionError):
            list_directory(store, run_id, token, "external", {"path": "external"})
    finally:
        junction.rmdir()


async def test_model_can_discover_actual_file_names(workspace_tmp_path):
    (workspace_tmp_path / "discovered.txt").touch()
    store, run_id = make_run(workspace_tmp_path)

    class DiscoveryProvider(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            if messages[-1]["role"] == "user":
                assert any(t["function"]["name"] == "workspace_list_directory" for t in tools)
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "discover",
                            "type": "function",
                            "function": {
                                "name": "workspace_list_directory",
                                "arguments": '{"path":"."}',
                            },
                        }
                    ],
                }
            else:
                result = json.loads(messages[-1]["content"])
                assert any(item["name"] == "discovered.txt" for item in result["entries"])
                message = {"role": "assistant", "content": "Found discovered.txt"}
            return {"choices": [{"message": message}]}

    provider = DiscoveryProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "verifying"
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.parametrize(
    "failure,expected,success", [(11, 3, True), (13, 1, False), (2, 1, False), (11, 3, False)]
)
def test_listing_retry_policy(workspace_tmp_path, monkeypatch, failure, expected, success):
    from xaa.runtime import directory_listing as listing

    store, run_id = make_run(workspace_tmp_path)
    token = store.acquire_lease(run_id, "worker")
    attempts = []
    delays = []
    original = listing._entries

    def flaky(target, root):
        attempts.append(target)
        if not success or len(attempts) < 3:
            raise OSError(failure, "injected read error")
        return original(target, root)

    monkeypatch.setattr(listing, "_entries", flaky)
    monkeypatch.setattr(listing.time, "sleep", delays.append)
    if success:
        list_directory(store, run_id, token, "retry", {"path": "."})
        assert not store.unresolved_tool_calls(run_id)
    else:
        with pytest.raises(OSError):
            list_directory(store, run_id, token, "retry", {"path": "."})
        assert len(store.unresolved_tool_calls(run_id)) == 1
    assert len(attempts) == expected
    assert delays == ([0.05, 0.1] if expected == 3 else [])
    events = store.list_events(run_id)
    assert sum(e["event_type"] == "tool.intended" for e in events) == 1


def test_listing_retry_rechecks_ownership(workspace_tmp_path, monkeypatch):
    from xaa.runtime import directory_listing as listing
    from xaa.storage.runs import LeaseLost

    store, run_id = make_run(workspace_tmp_path)
    token = store.acquire_lease(run_id, "worker")
    attempts = []

    def fail(target, root):
        attempts.append(target)
        raise OSError(11, "transient")

    monkeypatch.setattr(listing, "_entries", fail)
    monkeypatch.setattr(listing.time, "sleep", lambda _: store.release_lease(run_id, token))
    with pytest.raises(LeaseLost):
        list_directory(store, run_id, token, "fenced", {"path": "."})
    assert len(attempts) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing contention")
def test_real_directory_sharing_contention(workspace_tmp_path, monkeypatch):
    import ctypes

    from xaa.runtime import directory_listing as listing

    directory = workspace_tmp_path / "locked"
    directory.mkdir()
    (directory / "visible.txt").touch()
    store, run_id = make_run(workspace_tmp_path)
    token = store.acquire_lease(run_id, "worker")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create.restype = ctypes.c_void_p
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    handle = create(str(directory), 1, 0, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value
    released = False

    def release_after_contention(delay):
        nonlocal released
        assert delay == 0.05
        assert close(handle)
        released = True

    monkeypatch.setattr(listing.time, "sleep", release_after_contention)
    try:
        result = list_directory(store, run_id, token, "contention", {"path": "locked"})
        assert released
        assert {"name": "visible.txt", "kind": "file"} in result["entries"]
        assert sum(e["event_type"] == "tool.read_retry" for e in store.list_events(run_id)) == 1
        assert not store.unresolved_tool_calls(run_id)
    finally:
        if not released:
            close(handle)

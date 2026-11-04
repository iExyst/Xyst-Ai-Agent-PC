import asyncio
import json
import os
import subprocess

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.workspace_search import search_workspace
from xaa.storage.runs import RunStore


def setup(root):
    workspace = root / "workspace"
    workspace.mkdir()
    store = RunStore(root / "search.sqlite3")
    run_id = store.create_run(
        objective="Find evidence",
        acceptance_criteria=[],
        workspace=str(workspace),
        mode="observe",
        model="fixture",
    )["id"]
    return workspace, store, run_id, store.acquire_lease(run_id, "search")


async def test_literal_text_and_filename_search_keep_only_parent_result(workspace_tmp_path):
    workspace, store, run_id, token = setup(workspace_tmp_path)
    (workspace / "src").mkdir()
    (workspace / "src" / "Evidence.txt").write_text("First\nStraße evidence\n", encoding="utf-8")
    result = await search_workspace(store, run_id, token, "text", {"query": "STRASSE"})
    assert result["matches"] == [
        {
            "path": str(workspace.joinpath("src", "Evidence.txt").relative_to(workspace)),
            "line": 2,
            "excerpt": "Straße evidence",
        }
    ]
    assert result["truncated"] is False
    names = await search_workspace(
        store, run_id, token, "names", {"query": "evidence", "target": "filename"}
    )
    assert len(names["matches"]) == 1
    assert names["scanned_files"] == 1
    assert len(store.conversation_messages(run_id)) == 2
    assert not store.unresolved_tool_calls(run_id)


async def test_search_reports_match_file_byte_and_exclusion_limits(workspace_tmp_path):
    workspace, store, run_id, token = setup(workspace_tmp_path)
    (workspace / "many.txt").write_text("needle\n" * 10)
    result = await search_workspace(
        store, run_id, token, "limited", {"query": "needle", "limit": 2}
    )
    assert len(result["matches"]) == 2 and "match_limit" in result["limits_reached"]
    (workspace / "many.txt").write_bytes(b"x" * 70000 + b"late needle")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "hidden.txt").write_text("late needle")
    result = await search_workspace(store, run_id, token, "bytes", {"query": "late needle"})
    assert result["matches"] == [] and result["truncated"] is True
    assert "file_bytes_limit" in result["limits_reached"]
    assert result["excluded_directories"] == 1


async def test_search_cancellation_preserves_unresolved_parent_intent(workspace_tmp_path):
    _, store, run_id, token = setup(workspace_tmp_path)
    task = asyncio.create_task(
        search_workspace(store, run_id, token, "cancelled", {"query": "needle"})
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.unresolved_tool_calls(run_id)[0]["id"] == "cancelled"
    assert store.conversation_messages(run_id) == []


async def test_search_skips_external_link_and_rejects_direct_escape(workspace_tmp_path):
    workspace, store, run_id, token = setup(workspace_tmp_path)
    outside = workspace_tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("secret needle")
    link = workspace / "external"
    if os.name == "nt":
        await asyncio.to_thread(
            subprocess.run,
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        result = await search_workspace(store, run_id, token, "root", {"query": "needle"})
        assert result["matches"] == [] and result["skipped_links"] == 1
        with pytest.raises(PermissionError):
            await search_workspace(
                store, run_id, token, "escape", {"query": "needle", "path": "external"}
            )
        assert not store.unresolved_tool_calls(run_id)
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


async def test_coordinator_passes_actual_search_evidence_to_model(workspace_tmp_path):
    workspace, store, run_id, token = setup(workspace_tmp_path)
    (workspace / "unknown.txt").write_text("needle actual evidence")
    store.release_lease(run_id, token)

    class Searcher(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            if messages[-1]["role"] == "user":
                assert any(tool["function"]["name"] == "workspace_search" for tool in tools)
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "search",
                            "type": "function",
                            "function": {
                                "name": "workspace_search",
                                "arguments": '{"query":"needle"}',
                            },
                        }
                    ],
                }
            else:
                assert json.loads(messages[-1]["content"])["matches"][0]["path"] == "unknown.txt"
                message = {"role": "assistant", "content": "Found evidence in unknown.txt"}
            return {"choices": [{"message": message}]}

    provider = Searcher()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "verifying"
    assert len(store.conversation_messages(run_id)) == 3


@pytest.mark.parametrize("kind,reason", [("file", "file_limit"), ("directory", "directory_limit")])
async def test_search_scan_counts_are_bounded(workspace_tmp_path, kind, reason):
    workspace, store, run_id, token = setup(workspace_tmp_path)
    for index in range(130 if kind == "file" else 35):
        item = workspace / f"item-{index}"
        if kind == "file":
            item.touch()
        else:
            item.mkdir()
    result = await search_workspace(
        store, run_id, token, "bounded", {"query": "absent-needle", "target": "filename"}
    )
    assert result["truncated"] is True
    assert reason in result["limits_reached"]
    assert result["scanned_files"] <= 128 and result["scanned_directories"] <= 32


async def test_search_match_output_is_bounded_by_utf8_bytes(workspace_tmp_path):
    workspace, store, run_id, token = setup(workspace_tmp_path)
    (workspace / "unicode.txt").write_bytes((("needle " + "😀" * 300 + "\n") * 50).encode())
    result = await search_workspace(
        store, run_id, token, "output", {"query": "needle", "limit": 50}
    )
    assert "output_bytes_limit" in result["limits_reached"]
    assert len(result["matches"]) < 50
    assert (
        sum(len(json.dumps(match, ensure_ascii=False).encode()) for match in result["matches"])
        <= 32768
    )

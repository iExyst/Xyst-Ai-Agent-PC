import hashlib

import pytest
from xaa.runtime.executor import ToolPreconditionError, WorkspaceExecutor
from xaa.runtime.unified_patch import apply_unified_patch
from xaa.storage.runs import RunStore


def test_multiple_hunks_and_zero_length_ranges():
    patch = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+A\n@@ -3 +3 @@\n-c\n+C\n"
    assert apply_unified_patch("a\nb\nc\nd\n", patch, "f") == "A\nb\nC\nd\n"
    assert (
        apply_unified_patch("a\n", "--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+first\n", "f") == "first\na\n"
    )
    assert apply_unified_patch("a\n", "--- a/f\n+++ b/f\n@@ -1 +0,0 @@\n-a\n", "f") == ""


def test_exact_line_endings_and_no_final_newline():
    with pytest.raises(ValueError, match="lines must end"):
        apply_unified_patch("old\n", "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new", "f")
    patch = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n"
    assert apply_unified_patch("old", patch, "f") == "new"
    patch = "--- a/f\r\n+++ b/f\r\n@@ -1 +1 @@\r\n-old\r\n+new\r\n"
    assert apply_unified_patch("old\r\n", patch, "f") == "new\r\n"


@pytest.mark.parametrize(
    "patch",
    [
        "--- a/other\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n",
        "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-wrong\n+b\n",
        "--- a/f\n+++ b/f\n@@ -1,2 +1 @@\n-a\n+b\n",
        "--- a/f\n+++ b/f\n@@ -2 +1 @@\n-a\n+b\n",
        "--- a/f\n+++ b/f\n@@ -1 +2 @@\n-a\n+b\n",
        "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n--- a/other\n+++ b/other\n",
        "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n@@ -1 +1 @@\n-a\n+c\n",
        "--- a/f\n+++ b/f\n\\ No newline at end of file\n",
    ],
)
def test_rejects_invalid_patch_without_fuzzy_matching(patch):
    with pytest.raises(ValueError):
        apply_unified_patch("a\n", patch, "f")


def test_patch_byte_and_hunk_limits():
    with pytest.raises(ValueError, match="64 KiB"):
        apply_unified_patch("", "\u2713" * 22000, "f")
    with pytest.raises(ValueError, match="64 KiB"):
        apply_unified_patch("a" * 65536, "--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+x\n", "f")
    patch = "--- a/f\n+++ b/f\n" + "".join(f"@@ -0,0 +{i},0 @@\n" for i in [0] * 129)
    with pytest.raises(ValueError, match="128 hunks"):
        apply_unified_patch("", patch, "f")


def test_late_patch_failure_is_journaled_without_partial_write(workspace_tmp_path):
    target = workspace_tmp_path / "f"
    original = b"a\nb\nc\n"
    target.write_bytes(original)
    store = RunStore(workspace_tmp_path / "db.sqlite3")
    run = store.create_run(
        objective="Patch",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )
    token = store.acquire_lease(run["id"], "worker")
    executor = WorkspaceExecutor(store)
    args = {
        "path": "f",
        "expected_sha256": hashlib.sha256(original).hexdigest(),
        "patch": "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+A\n@@ -3 +3 @@\n-wrong\n+C\n",
    }
    with pytest.raises(ToolPreconditionError) as caught:
        executor.apply_patch(run["id"], token, "bad", args, idempotency_key="bad")
    assert caught.value.result["effect"] == "not_started"
    assert target.read_bytes() == original
    assert not store.unresolved_tool_calls(run["id"])
    assert not any(e["event_type"] == "tool.executing" for e in store.list_events(run["id"]))
    args["patch"] = args["patch"].replace("-wrong", "-c")
    executor.apply_patch(run["id"], token, "good", args, idempotency_key="good")
    assert target.read_bytes() == b"A\nb\nC\n"
    assert not store.unresolved_tool_calls(run["id"])

"""Verify a real model read/hash/edit cycle against isolated fixture files."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider, ProviderError
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    (root / "config.txt").write_bytes(b"value=1\n")
    (root / "neighbor.txt").write_bytes(b"preserve-this-file")
    expected = b"value=2\n"
    store = RunStore(root / "runs.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen3-0.6b")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance = None
    method = os.environ.get("LM_STUDIO_EDIT_TOOL", "write")
    run = store.create_run(
        objective=(
            "Read config.txt, then call workspace_apply_patch with the returned sha256. Supply a single-file unified patch with headers --- a/config.txt and +++ b/config.txt, hunk @@ -1 +1 @@, removed line value=1 and added line value=2. Preserve the final newline. Report the result."
            if method == "patch"
            else "Read config.txt. First call workspace_replace_text using the returned sha256, old_text deliberately_missing_text and new_text value=2. This must fail without writing. After receiving match_count failure, submit a NEW workspace_replace_text using old_text value=1 and new_text value=2 with the actual file hash. Preserve the final newline and report the result."
            if method == "rejected_replace"
            else 'First call workspace_write_file on config.txt with expected_sha256 set to 64 zero characters and content equal to the JSON string "value=2\\n". This deliberately stale hash must be rejected without writing. After receiving the failure, call workspace_read_file on config.txt, then submit a NEW workspace_write_file call using the actual returned sha256 and exact content "value=2\\n", including the final newline. Report the result.'
            if method == "stale"
            else "Read config.txt using workspace_read_file. Then use workspace_replace_text to replace old_text value=1 with new_text value=2, copying the exact returned sha256 into expected_sha256. Preserve all other content, including the final newline. Report the result."
            if method == "replace"
            else 'First call workspace_read_file with path config.txt. Then use workspace_write_file with path config.txt and content equal to the JSON string "value=2\\n". The content must include the final newline character, not a literal backslash and n. Copy the exact sha256 returned by the read tool into expected_sha256. Do not guess the hash. Report the edit result.'
        ),
        acceptance_criteria=[
            "config.txt is exactly value=2 followed by a newline",
            "neighbor.txt remains unchanged",
        ],
        workspace=str(root),
        file_checks=[{"path": "config.txt", "expected_content": "value=2\n"}],
        mode="workspace",
        model=model,
    )
    failure = None
    try:
        instance = await provider.load_model(
            model, context_length=int(os.environ.get("LM_STUDIO_CONTEXT_LENGTH", "16384"))
        )
        try:
            await Coordinator(store, provider).execute(run["id"])
        except (ProviderError, ValueError, KeyError, OSError, TimeoutError) as exc:
            failure = f"{type(exc).__name__}: {exc}"
        saved = store.get_run(run["id"])
        events = store.list_events(run["id"])
        writes = [
            event
            for event in events
            if event["event_type"] == "tool.intended"
            and event["payload"].get("tool") == "workspace.write_file"
        ]
        contents = (root / "config.txt").read_bytes()
        known_failures = [
            event
            for event in events
            if event["event_type"] == "tool.failed"
            and event["payload"].get("code")
            == ("match_count" if method == "rejected_replace" else "file_changed")
        ]
        neighbor_preserved = (root / "neighbor.txt").read_bytes() == b"preserve-this-file"
        patch_calls = [
            call
            for event in events
            if event["event_type"] == "model.message"
            for call in event["payload"].get("message", {}).get("tool_calls", [])
            if call["function"]["name"] == "workspace_apply_patch"
        ]
        passed = bool(
            saved
            and saved["state"] == "verifying"
            and contents == expected
            and neighbor_preserved
            and writes
            and (method != "patch" or patch_calls)
            and not store.unresolved_tool_calls(run["id"])
            and (method not in {"stale", "rejected_replace"} or len(known_failures) >= 1)
        )
        report = {
            "requested_edit_method": method,
            "passed": passed,
            "model": model,
            "run_id": run["id"],
            "actual_content": contents.decode(),
            "neighbor_preserved": neighbor_preserved,
            "write_calls": len(writes),
            "patch_proposals": len(patch_calls),
            "known_prewrite_failures": len(known_failures),
            "state": saved["state"] if saved else None,
            "error": failure,
        }
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))
        if not passed:
            raise RuntimeError("Live model edit acceptance failed.")
    finally:
        if instance is not None:
            await provider.unload_model(instance)
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())

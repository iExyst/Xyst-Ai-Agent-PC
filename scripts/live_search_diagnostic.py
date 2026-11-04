"""Verify real local-model search followed by a file read for unseen evidence."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    filename = f"evidence-{uuid4().hex}.txt"
    marker = f"MARKER-{uuid4().hex}"
    code = f"CODE-{uuid4().hex}"
    fixture = f"{marker}\nVerification code: {code}\n"
    (workspace / filename).write_bytes(fixture.encode())
    (workspace / "decoy.txt").write_bytes(b"No matching marker here.\n")
    store = RunStore(root / "search.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    streaming = os.environ.get("XAA_STREAM_RESPONSES") == "1"
    instance = None
    report: dict[str, object] = {"passed": False, "model": model, "evidence": str(root)}
    try:
        instance = await provider.load_model(model, context_length=16384)
        run = store.create_run(
            objective=f"Use workspace_search to find the literal text {marker} anywhere in this workspace. Then use workspace_read_file to read the matching file and report its exact verification code. Do not modify any file.",
            acceptance_criteria=["Find the marker and report the actual unseen verification code."],
            workspace=str(workspace),
            mode="observe",
            model=model,
        )
        await Coordinator(store, provider, stream_responses=streaming).execute(run["id"])
        history = store.conversation_messages(run["id"])
        calls = [
            call["function"]["name"]
            for message in history
            for call in message.get("tool_calls", [])
        ]
        answer = history[-1].get("content", "") if history else ""
        saved = store.get_run(run["id"])
        search_result = json.loads(
            next(message["content"] for message in history if message["role"] == "tool")
        )
        delta_count = sum(
            event["event_type"] == "model.delta" for event in store.list_events(run["id"])
        )
        passed = (
            saved is not None
            and saved["state"] == "verifying"
            and code in answer
            and calls == ["workspace_search", "workspace_read_file"]
            and search_result["matches"][0]["path"] == filename
            and code not in json.dumps(search_result)
            and not store.unresolved_tool_calls(run["id"])
            and (workspace / filename).read_bytes() == fixture.encode()
            and (not streaming or delta_count > 0)
        )
        report.update(
            passed=passed,
            run_id=run["id"],
            tool_calls=calls,
            answer=answer,
            search_result=search_result,
            streaming_enabled=streaming,
            content_delta_events=delta_count,
        )
        if not passed:
            raise RuntimeError("Real search/read acceptance failed.")
    except Exception as exc:
        report["error"] = type(exc).__name__
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())

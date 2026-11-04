"""Kill a real read worker before result commit, then resume with a real local model."""

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore


class InterruptedStore(RunStore):
    def record_tool_result(
        self,
        run_id: str,
        token: str,
        call_id: str,
        result: dict[str, Any],
        *,
        internal: bool = False,
    ) -> None:
        (self.path.parent / "read.marker").write_text(
            "read finished; result not committed", encoding="utf-8"
        )
        time.sleep(60)
        raise RuntimeError("The parent must kill this process.")


def child(root: Path, run_id: str) -> None:
    store = InterruptedStore(root / "runs.sqlite3")
    token = store.acquire_lease(run_id, "interrupted-reader", seconds=1)
    assert token is not None
    store.transition(run_id, RunState.PLANNING, owner=token)
    store.transition(run_id, RunState.RUNNING, owner=token)
    WorkspaceExecutor(store).read_file(
        run_id, token, "interrupted-read", {"path": "input.txt"}, idempotency_key="interrupted-read"
    )


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    code = f"READ-{uuid4().hex}"
    target = workspace / "input.txt"
    target.write_bytes(code.encode())
    stamp = target.stat().st_mtime_ns
    store = RunStore(root / "runs.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    run = store.create_run(
        objective="Read input.txt using workspace_read_file and report its exact code. If an earlier read was interrupted, request a fresh read.",
        acceptance_criteria=["Report the exact code read from the file"],
        workspace=str(workspace),
        mode="observe",
        model=model,
    )
    token = store.acquire_lease(run["id"], "fixture-setup")
    assert token is not None
    store.append_worker_event(
        run["id"],
        token,
        "model.message",
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "interrupted-read",
                        "type": "function",
                        "function": {
                            "name": "workspace_read_file",
                            "arguments": '{"path":"input.txt"}',
                        },
                    }
                ],
            }
        },
    )
    store.release_lease(run["id"], token)
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance = None
    report: dict[str, Any] = {"passed": False, "model": model, "seeded_initial_tool_proposal": True}
    try:
        with (root / "worker.log").open("w", encoding="utf-8") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                str(root),
                run["id"],
                stdout=log,
                stderr=log,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            try:
                for _ in range(100):
                    if (root / "read.marker").exists():
                        break
                    if process.returncode is not None:
                        raise RuntimeError("Read worker exited before its interruption boundary.")
                    await asyncio.sleep(0.05)
                else:
                    raise TimeoutError("Read worker never reached its boundary.")
                process.kill()
                await asyncio.wait_for(process.wait(), 5)
            finally:
                if process.returncode is None:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 5)
        saved = store.get_run(run["id"])
        assert saved is not None
        remaining = (
            datetime.fromisoformat(saved["lease_expires_at"]) - datetime.now(UTC)
        ).total_seconds()
        if remaining > 0:
            await asyncio.sleep(min(remaining + 0.05, 2))
        store.recover_abandoned_runs()
        assert store.get_run(run["id"])["state"] == "blocked"
        store.transition(run["id"], RunState.QUEUED, reason="operator_requested_resume")
        instance = await provider.load_model(model, context_length=16384)
        await Coordinator(store, provider).execute(run["id"])
        events = store.list_events(run["id"])
        failures = [
            e
            for e in events
            if e["event_type"] == "tool.failed" and e["payload"].get("code") == "interrupted_read"
        ]
        fresh_ids = {
            e["payload"]["call_id"]
            for e in events
            if e["event_type"] == "tool.intended"
            and e["payload"].get("tool") == "workspace.read_file"
            and e["payload"]["call_id"] != "interrupted-read"
        }
        fresh = [
            e
            for e in events
            if e["event_type"] == "tool.completed" and e["payload"]["call_id"] in fresh_ids
        ]
        history = store.conversation_messages(run["id"])
        answer = history[-1].get("content", "")
        passed = bool(
            len(failures) == 1
            and fresh
            and code in answer
            and store.get_run(run["id"])["state"] == "verifying"
            and not store.unresolved_tool_calls(run["id"])
            and target.read_bytes() == code.encode()
            and target.stat().st_mtime_ns == stamp
        )
        report.update(
            passed=passed,
            run_id=run["id"],
            initial_recovery_state="blocked",
            interrupted_results=len(failures),
            fresh_read_results=len(fresh),
            fixture_unchanged=target.read_bytes() == code.encode(),
            mtime_unchanged=target.stat().st_mtime_ns == stamp,
            state=store.get_run(run["id"])["state"],
        )
        if not passed:
            raise RuntimeError("Read-crash recovery acceptance failed.")
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))
        if instance is not None:
            await provider.unload_model(instance)
        await provider.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), sys.argv[3])
    else:
        asyncio.run(main())

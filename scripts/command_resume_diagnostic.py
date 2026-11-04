"""Real local model continues from a recovered command result without replay."""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from canonical_context_diagnostic import AuditedProvider
from command_recovery_diagnostic import CHILD
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore


class ResumeProvider(AuditedProvider):
    def __init__(self) -> None:
        super().__init__()
        self.responses: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> dict[str, Any]:
        response = await super().chat(**kwargs)
        choice = response["choices"][0]
        message = choice["message"]
        self.responses.append(
            {
                "finish_reason": choice.get("finish_reason"),
                "public_content_length": len(message.get("content") or ""),
                "tool_calls": len(message.get("tool_calls") or []),
                "extra_field_names": sorted(set(message) - {"role", "content", "tool_calls"}),
            }
        )
        return response


async def diagnose(distribution: str, model: str) -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    project = root / "project"
    project.mkdir(parents=True)
    nonce = "RECOVERED-CODE-" + uuid4().hex
    (project / "fixture.txt").write_text(nonce, encoding="utf-8")
    store = RunStore(root / "data" / "db.sqlite3")
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
    provider = ResumeProvider()
    instance = None
    try:
        inventory = await provider.models()
        if not any(item.id == model for item in inventory):
            raise RuntimeError("Selected model is not installed; no download is permitted")
        instance = await provider.load_model(model, context_length=32768)
        run = store.create_run(
            objective="A command was interrupted after producing output. Use only its recovered stdout. Output the entire recovered stdout line exactly, including the literal RECOVERED-CODE- prefix and every character following it. Do not run another tool or save a plan. Do not add any other text.",
            acceptance_criteria=[
                "Report the exact code in the recovered stdout without repeating execution."
            ],
            workspace=str(project),
            mode="workspace",
            model=instance,
            max_iterations=3,
            max_seconds=180,
            max_output_tokens=1024,
            max_total_tokens=30000,
            command_grant={
                "distribution": distribution,
                "profile_sha256": transport.profile_sha256,
            },
        )
        run_id = run["id"]
        owner = store.acquire_lease(run_id, "crashing-controller", seconds=120)
        if owner is None:
            raise RuntimeError("No fixture lease")
        store.transition(run_id, RunState.PLANNING, owner=owner)
        store.transition(run_id, RunState.RUNNING, owner=owner)
        argv = [
            "/usr/bin/python3",
            "-I",
            "-c",
            "import pathlib;print(pathlib.Path('fixture.txt').read_text())",
        ]
        store.append_worker_event(
            run_id,
            owner,
            "model.message",
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "command",
                            "type": "function",
                            "function": {
                                "name": "workspace_run_command",
                                "arguments": json.dumps({"argv": argv}),
                            },
                        }
                    ],
                },
                "actor": "scripted_recovery_fixture_not_model_generated",
                # Constructing this history fixture made no inference request.
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )
        child = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-I", "-c", CHILD],
            input=json.dumps(
                {
                    "database": str(store.path),
                    "run_id": run_id,
                    "owner": owner,
                    "distribution": distribution,
                }
            ).encode(),
            capture_output=True,
            timeout=60,
            creationflags=0x08000000,
        )
        assert child.returncode == 73
        with store.connection() as connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run_id,)
            )
        store.recover_abandoned_runs()
        stopped = store.get_run(run_id)
        assert stopped is not None and stopped["state"] == "blocked"
        store.transition(run_id, RunState.QUEUED)
        await Coordinator(RunStore(store.path), provider).execute(run_id)
        saved = store.get_run(run_id)
        assert saved is not None and saved["state"] == "verifying"
        messages = store.conversation_messages(run_id)
        assert nonce in (messages[-1].get("content") or "")
        replies = [
            message
            for message in messages
            if message["role"] == "tool" and message["tool_call_id"] == "command"
        ]
        assert len(replies) == 1 and json.loads(replies[0]["content"])["result_reconciled"] is True
        events = store.list_events(run_id)
        assert sum(event["event_type"] == "tool.executing" for event in events) == 1
        recovered = next(
            event["sequence"] for event in events if event["event_type"] == "execution.completed"
        )
        diagnosed = next(
            event["sequence"]
            for event in events
            if event["event_type"] == "diagnostic.inference.started"
        )
        assert recovered < diagnosed
        assert not store.unresolved_tool_calls(run_id)
        assert (project / "fixture.txt").read_text(encoding="utf-8") == nonce
        report = {
            "passed": True,
            "model": model,
            "distribution": distribution,
            "run_id": run_id,
            "state": saved["state"],
            "exact_recovered_code_reported": True,
            "tool_replies": 1,
            "launch_events": 1,
            "unresolved_commands": 0,
            "recovered_before_provider_diagnostics": True,
            "counts": provider.rows,
            "response_metadata": provider.responses,
            "initial_tool_proposal_actor": "scripted_recovery_fixture_not_model_generated",
            "continuation_actor": "real_local_model",
            "ui_or_model_command_tool_enabled": False,
            "scope": "Source coordinator resume; no native UI or autonomous command-generation claim",
        }
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Real model command resume acceptance passed: {root}")
    finally:
        if not (root / "report.json").exists():
            (root / "report.json").write_text(
                json.dumps(
                    {
                        "passed": False,
                        "counts": provider.rows,
                        "response_metadata": provider.responses,
                        "scope": "Failed source resume diagnostic; inspect saved run state",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    asyncio.run(diagnose(args.distribution, args.model))

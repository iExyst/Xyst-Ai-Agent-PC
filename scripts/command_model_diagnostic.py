"""Real model proposes a command, consumes actual WSL output and returns a candidate."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from command_resume_diagnostic import ResumeProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore


async def diagnose(distribution: str, model: str) -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    project = root / "project"
    project.mkdir(parents=True)
    nonce = "CHECK-PASSED-" + uuid4().hex
    script = (
        "from pathlib import Path\nPath('private-generated.txt').write_text('private only')\nprint("
        + repr(nonce)
        + ")\n"
    )
    (project / "check.py").write_text(script, encoding="utf-8")
    store = RunStore(root / "data" / "db.sqlite3")
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
    provider = ResumeProvider()
    instance = None
    report: dict[str, Any] = {
        "passed": False,
        "scope": "Source coordinator and real LM Studio/WSL; internal user grant, no HTTP/native UI authorization acceptance",
    }
    try:
        if not any(item.id == model for item in await provider.models()):
            raise RuntimeError("Selected model is not installed; no automatic download")
        instance = await provider.load_model(model, context_length=32768)
        run = store.create_run(
            objective='Please execute the existing project check.py using workspace_run_command with argv ["/usr/bin/python3", "-I", "check.py"] and max_seconds 10. Then tell me exactly what it printed in stdout. Your final answer must contain the entire stdout line including the CHECK-PASSED- prefix and every character following it. Use only this one command tool call. Do not read or edit files or save a plan.',
            acceptance_criteria=[
                "A real model proposes the structured command, receives its actual result and reports the exact stdout."
            ],
            workspace=str(project),
            mode="workspace",
            model=instance,
            max_iterations=4,
            max_seconds=180,
            max_total_tokens=30000,
            max_output_tokens=2048,
            command_grant={
                "distribution": distribution,
                "profile_sha256": transport.profile_sha256,
            },
        )
        report["run_id"] = run["id"]
        await Coordinator(store, provider).execute(run["id"])
        events = store.list_events(run["id"])
        messages = [
            event["payload"]["message"]
            for event in events
            if event["event_type"] == "model.message"
        ]
        proposals = [
            call
            for message in messages
            for call in message.get("tool_calls", [])
            if call["function"]["name"] == "workspace_run_command"
        ]
        launches = [event for event in events if event["event_type"] == "tool.executing"]
        completed = [event for event in events if event["event_type"] == "execution.completed"]
        saved = store.get_run(run["id"])
        assert saved is not None and saved["state"] == "verifying"
        assert len(proposals) == 1 and json.loads(proposals[0]["function"]["arguments"])[
            "argv"
        ] == ["/usr/bin/python3", "-I", "check.py"]
        assert len(launches) == 1 and len(completed) == 1
        assert messages[-1].get("content", "").strip() == nonce
        assert not store.unresolved_tool_calls(run["id"])
        assert (project / "check.py").read_text(encoding="utf-8") == script
        assert not (project / "private-generated.txt").exists()
        report.update(
            passed=True,
            model=model,
            distribution=distribution,
            profile_sha256=transport.profile_sha256,
            state=saved["state"],
            model_command_proposals=1,
            launch_events=1,
            command_results=1,
            answer_only_retries=sum(
                event["event_type"] == "model.answer_requested" for event in events
            ),
            response_repair_requests=sum(
                event["event_type"] == "model.response_requested" for event in events
            ),
            exact_stdout_reported=True,
            original_project_unchanged=True,
            unresolved_commands=0,
            counts=provider.rows,
            response_metadata=provider.responses,
        )
    finally:
        report["counts"] = provider.rows
        report["response_metadata"] = provider.responses
        try:
            if instance is not None:
                await provider.unload_model(instance)
                report["owned_model_unloaded"] = True
        finally:
            await provider.close()
            (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Command model acceptance evidence: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    asyncio.run(diagnose(args.distribution, args.model))

"""Real local model consumes a selected immutable procedure snapshot in a bounded trial."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from canonical_context_diagnostic import AuditedProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore
from xaa.storage.skills import SkillDraft, SkillRevision, SkillStore, SkillTool


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    nonce, label = "TRIAL-CODE-" + uuid4().hex, "TRIAL-LABEL-" + uuid4().hex
    fixture = root / "input.txt"
    original = (nonce + "\n").encode()
    fixture.write_bytes(original)
    stamp = fixture.stat().st_mtime_ns
    store, provider = RunStore(root / "runs.sqlite3"), AuditedProvider()
    skills = SkillStore(store)
    draft = SkillDraft(
        name="Read exact evidence",
        content="Use workspace_read_file to read input.txt. Report its exact code and include this exact label in your final answer: "
        + label
        + ". Do not save a plan or modify files.",
        required_tools=[SkillTool(name="workspace.read_file", version="1")],
        required_permissions=["workspace.read"],
    )
    skill = skills.create(draft)
    instance = None
    report: dict[str, Any] = {
        "passed": False,
        "scope": "real model explicit candidate trial; no activation or evaluation claim",
    }
    try:
        instance = await provider.load_model("qwen/qwen3.5-9b", context_length=32768)
        run = store.create_run(
            objective="Read input.txt and report the exact code from it.",
            acceptance_criteria=[
                "Read the actual file and follow the selected procedure's reporting format."
            ],
            workspace=str(root),
            mode="observe",
            model=instance,
            max_iterations=4,
            max_seconds=90,
            max_output_tokens=1024,
            max_total_tokens=20000,
            procedure_trial={"skill_id": skill["id"], "version": 1},
        )
        skills.revise(
            skill["id"],
            SkillRevision.model_validate(
                {**draft.model_dump(), "content": "REVISION_NOT_SELECTED", "expected_version": 1}
            ),
        )
        await Coordinator(store, provider).execute(run["id"])
        saved = store.get_run(run["id"])
        assert saved is not None
        events = store.list_events(run["id"])
        final = store.conversation_messages(run["id"])[-1].get("content") or ""
        selected = store.procedure_trial(run["id"])
        assert selected is not None
        report.update(
            run_id=run["id"],
            state=saved["state"],
            selected_version=selected["version"],
            snapshot_sha256=selected["snapshot_sha256"],
            library_latest_version=skills.get(skill["id"])["latest_version"],
            library_state=skills.get(skill["id"])["lifecycle_state"],
            active_version=skills.get(skill["id"])["active_version"],
            exact_file_code_reported=nonce in final,
            procedure_only_label_reported=label in final,
            fixture_unchanged=fixture.read_bytes() == original
            and fixture.stat().st_mtime_ns == stamp,
            context_load_events=sum(e["event_type"] == "procedure.context_loaded" for e in events),
            counts=provider.rows,
        )
        report["passed"] = (
            saved["state"] == "verifying"
            and nonce in final
            and label in final
            and report["fixture_unchanged"]
            and report["context_load_events"] == 1
            and selected["version"] == 1
            and skills.get(skill["id"])["active_version"] is None
            and any(e["event_type"] == "tool.completed" for e in events)
            and not store.unresolved_tool_calls(run["id"])
        )
        if not report["passed"]:
            raise RuntimeError("Real procedure trial acceptance failed.")
    finally:
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

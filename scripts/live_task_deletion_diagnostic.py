"""Use the selected live model to propose and explicitly review a private deletion."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.task_review import TaskReviewDecision, candidate_evidence, review_candidate
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


async def diagnose(root: Path, model: str) -> None:
    root = root.resolve()
    root.relative_to((Path.cwd() / ".acceptance").resolve())
    project = root / "project"
    project.mkdir(parents=True, exist_ok=False)
    target = project / "obsolete.txt"
    original = b"disposable deletion target\n"
    neighbor = b"keep this neighboring file\n"
    target.write_bytes(original)
    (project / "neighbor.txt").write_bytes(neighbor)
    store = RunStore(root / "data" / "XAA" / "xaa.sqlite3")
    run_id = store.create_run(
        objective="Ask one scoped specialist to propose deleting obsolete.txt, review the exact deletion, and preserve neighbor.txt.",
        acceptance_criteria=[
            "The user-reviewed deletion removes obsolete.txt and preserves neighbor.txt"
        ],
        workspace=str(project),
        mode="workspace",
        model=model,
        max_iterations=8,
        max_seconds=240,
        max_output_tokens=2048,
    )["id"]
    owner = store.acquire_lease(run_id, "live-deletion-diagnostic", seconds=360)
    assert owner is not None
    TaskStore(store).save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id="remove",
                objective=(
                    "Read obsolete.txt and use workspace_delete_file to propose deleting it from your private working tree. "
                    "Copy expected_sha256 exactly from the read result. Do not edit, create, or delete any other file. "
                    "Then submit a concise candidate describing the private deletion proposal."
                ),
                acceptance_criteria=(
                    "Propose deletion of obsolete.txt from the private copy only",
                ),
                role="developer",
                permissions=("workspace.read", "workspace.write"),
                max_iterations=6,
                max_seconds=180,
                max_output_tokens=2048,
            )
        ],
    )
    # This acceptance isolates deletion/review behavior; exact SDK tokenizer
    # admission is covered by its own diagnostics and has intermittent failures.
    provider = LMStudioProvider(exact_context=False)
    instance = None
    report: dict[str, Any] = {
        "passed": False,
        "model": model,
        "root": str(root),
        "run_id": run_id,
        "scope": "Real local-model private deletion proposal and explicit scripted review; no autonomous reviewer or parent completion claim",
    }
    try:
        if not any(item.id == model for item in await provider.models()):
            raise RuntimeError(f"Selected model is not installed: {model}")
        instance = await provider.load_model(model, context_length=32768)
        store.transition(run_id, RunState.PLANNING, owner=owner)
        await Coordinator(store, provider).diagnose_run_tools(run_id, owner, model)
        store.transition(run_id, RunState.RUNNING, owner=owner)
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, owner, deadline=time.monotonic() + 240
        )
        assert statuses == [{"task_id": "remove", "status": "verifying"}], statuses
        from xaa.storage.task_workspaces import TaskWorkspaces

        with store.connection() as connection:
            _base, private_files = TaskWorkspaces(store).view(connection, run_id, "remove")
        changes = TaskWorkspaces(store).changes(run_id, "remove")
        assert "obsolete.txt" not in private_files and len(changes) == 1
        assert changes[0]["path"] == "obsolete.txt" and changes[0]["proposed_sha256"] is None
        assert (
            target.read_bytes() == original and (project / "neighbor.txt").read_bytes() == neighbor
        )
        store.transition(run_id, RunState.VERIFYING, owner=owner)
        store.release_lease(run_id, owner)
        evidence = candidate_evidence(store, run_id, "remove")["evidence"]
        assert any(item["path"] == "obsolete.txt" for item in evidence)
        decision = TaskReviewDecision(
            decision="accept",
            rationale="Inspected the real model's private deletion diff and confirmed the neighboring file must remain.",
            confirmed_criteria=[0],
            evidence_call_ids=[
                next(item["call_id"] for item in evidence if item["path"] == "obsolete.txt")
            ],
            changes_confirmed=True,
        )
        await review_candidate(store, run_id, "remove", decision)
        assert not target.exists() and (project / "neighbor.txt").read_bytes() == neighbor
        assert not store.unresolved_tool_calls(run_id)
        report.update(
            passed=True,
            specialist_status=statuses[0]["status"],
            model_proposed_private_deletion=True,
            original_unchanged_until_review=True,
            exact_user_reviewed_deletion_integrated=True,
            neighbor_preserved=True,
            parent_completion_claimed=False,
        )
    finally:
        store.release_lease(run_id, owner)
        try:
            if instance is not None:
                await provider.unload_model(instance)
                report["owned_model_unloaded"] = True
        finally:
            await provider.close()
            (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Live specialist deletion evidence: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".acceptance") / str(uuid4()))
    parser.add_argument("--model", default="qwen/qwen3.5-9b")
    arguments = parser.parse_args()
    asyncio.run(diagnose(arguments.root, arguments.model))

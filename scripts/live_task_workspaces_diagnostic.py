"""Real model specialists edit private variants; explicit fixture review resolves integration conflicts."""

import argparse
import ast
import asyncio
import hashlib
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
from xaa.runtime.task_integration import IntegrationResolution, preview
from xaa.runtime.task_review import TaskReviewDecision, candidate_evidence, review_candidate
from xaa.storage.runs import RunStore
from xaa.storage.task_workspaces import TaskWorkspaces
from xaa.storage.tasks import TaskStore


def verify_literal_module(data: bytes, value: str) -> None:
    tree = ast.parse(data.decode("utf-8"))
    assert len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef)
    function = tree.body[0]
    assert function.name == "value" and not function.decorator_list and not function.args.args
    assert len(function.body) == 1 and isinstance(function.body[0], ast.Return)
    assert (
        isinstance(function.body[0].value, ast.Constant) and function.body[0].value.value == value
    )


async def diagnose(root: Path, model: str, prepare_only: bool) -> None:
    root = root.resolve()
    root.relative_to((Path.cwd() / ".acceptance").resolve())
    project = root / "project"
    project.mkdir(parents=True, exist_ok=False)
    original = b'def value():\n    return "original"\n'
    (project / "shared.py").write_bytes(original)
    codes = {name: name + "-" + uuid4().hex for name in ("alpha", "beta")}
    store = RunStore(root / "data" / "XAA" / "xaa.sqlite3")
    run_id = store.create_run(
        objective="Compare alternative implementations in private specialist copies and explicitly choose which version to integrate.",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=model,
        max_iterations=16,
        max_seconds=480,
        max_output_tokens=2048,
    )["id"]
    parent = store.acquire_lease(run_id, "specialist-variant-fixture", seconds=300)
    assert parent is not None
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id=name,
                objective=f"Read shared.py, then use workspace_write_file to change value() so it returns exactly {code!r}. Keep only this one function in the module. Copy the sha256 from the read result. Your changes belong only in your private working tree. Supply the actual Python file text: after JSON decoding, content must have real line breaks and normal Python quotes, not literal backslash-n sequences or extra backslashes before quotes. After writing, read shared.py once to check the actual saved text, then submit a candidate naming shared.py and its new return value. Do not create other files or repeat completed writes.",
                acceptance_criteria=(
                    f"Private shared.py defines value() returning exactly {code}",
                ),
                role="developer",
                permissions=("workspace.read", "workspace.write"),
                max_iterations=6,
                max_seconds=180,
                max_output_tokens=2048,
            )
            for name, code in codes.items()
        ],
    )
    provider = LMStudioProvider(exact_context=True)
    instance = None
    report: dict[str, Any] = {
        "passed": False,
        "model": model,
        "root": str(root),
        "run_id": run_id,
        "codes": codes,
        "scope": "Real source model specialists with private snapshot working trees; scripted fixture plan and explicitly automated review, no autonomous reviewer/native inference claim",
        "fixture_review_automated": not prepare_only,
        "prepare_only": prepare_only,
    }
    try:
        if not any(item.id == model for item in await provider.models()):
            raise RuntimeError("Selected model is not installed; no automatic download")
        instance = await provider.load_model(model, context_length=32768)
        store.transition(run_id, RunState.PLANNING, owner=parent)
        await Coordinator(store, provider).diagnose_run_tools(run_id, parent, model)
        store.transition(run_id, RunState.RUNNING, owner=parent)
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 360
        )
        assert len(statuses) == 2 and all(item["status"] == "verifying" for item in statuses), (
            statuses
        )
        proposed: dict[str, str] = {}
        for name, code in codes.items():
            with store.connection() as connection:
                _, files = TaskWorkspaces(store).view(connection, run_id, name)
            assert set(files) == {"shared.py"}
            verify_literal_module(files["shared.py"], code)
            proposed[name] = hashlib.sha256(files["shared.py"]).hexdigest()
        assert (project / "shared.py").read_bytes() == original
        assert store.conversation_messages(run_id) == [] and not store.unresolved_tool_calls(run_id)
        store.transition(run_id, RunState.VERIFYING, owner=parent)
        store.release_lease(run_id, parent)
        report.update(
            statuses=statuses,
            proposed_sha256=proposed,
            original_unchanged_before_review=True,
            private_variants_independently_validated=True,
        )
        if not prepare_only:

            def decision(
                name: str, resolutions: list[IntegrationResolution] | None = None
            ) -> TaskReviewDecision:
                evidence = candidate_evidence(store, run_id, name)["evidence"]
                return TaskReviewDecision(
                    decision="accept",
                    rationale="Automated fixture review: private AST and complete diff independently inspected; explicit selected version for integration.",
                    confirmed_criteria=[0],
                    evidence_call_ids=[evidence[0]["call_id"]],
                    changes_confirmed=True,
                    resolutions=resolutions or [],
                )

            await review_candidate(store, run_id, "alpha", decision("alpha"))
            verify_literal_module((project / "shared.py").read_bytes(), codes["alpha"])
            refused = False
            try:
                await review_candidate(store, run_id, "beta", decision("beta"))
            except ValueError as error:
                assert "conflict" in str(error).lower()
                refused = True
            assert refused
            verify_literal_module((project / "shared.py").read_bytes(), codes["alpha"])
            owner = store.acquire_lease(run_id, "preview-fixture", seconds=180)
            assert owner is not None
            snapshot = await preview(store, run_id, "beta", owner)
            store.release_lease(run_id, owner)
            assert snapshot.changes[0].conflict
            await review_candidate(
                store,
                run_id,
                "beta",
                decision(
                    "beta",
                    [
                        IntegrationResolution(
                            path="shared.py",
                            expected_current_sha256=snapshot.changes[0].current_sha256,
                            choice="proposed",
                        )
                    ],
                ),
            )
            verify_literal_module((project / "shared.py").read_bytes(), codes["beta"])
            assert all(item["status"] == "completed" for item in tasks.records(run_id))
            report.update(
                conflict_refused_without_choice=True,
                chosen_beta_integrated=True,
                final_module_static_validation_passed=True,
                task_statuses=[
                    {"task_id": row["local_id"], "status": row["status"]}
                    for row in tasks.records(run_id)
                ],
            )
        assert store.conversation_messages(run_id) == [] and not store.unresolved_tool_calls(run_id)
        report.update(
            passed=True,
            parent_completion_claimed=False,
            unresolved_calls=0,
            parent_history_unchanged=True,
            usage=store.token_usage(run_id),
        )
    finally:
        store.release_lease(run_id, parent)
        try:
            if instance is not None:
                await provider.unload_model(instance)
                report["owned_model_unloaded"] = True
        finally:
            await provider.close()
            (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Private specialist workspace evidence: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".acceptance") / str(uuid4()))
    parser.add_argument("--model", default="qwen/qwen3.5-9b")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(diagnose(args.root, args.model, args.prepare_only))

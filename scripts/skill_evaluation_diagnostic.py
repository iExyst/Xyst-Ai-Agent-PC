"""Automated isolated acceptance of two real model trials and explicit review records."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from canonical_context_diagnostic import AuditedProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.run_review import RunReviewDecision, review_run
from xaa.storage.runs import RunStore
from xaa.storage.skills import SkillDraft, SkillEvaluation, SkillStore, SkillTool, TrialReview


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    store, provider = RunStore(root / "runs.sqlite3"), AuditedProvider()
    skills = SkillStore(store)
    label = "EVALUATION-LABEL-" + uuid4().hex
    skill = skills.create(
        SkillDraft(
            name="Read representative evidence",
            content="Read input.txt using workspace_read_file. Report its exact TRIAL-CODE and include this exact label: "
            + label
            + ". Do not modify files or save a plan.",
            required_tools=[SkillTool(name="workspace.read_file", version="1")],
            required_permissions=["workspace.read"],
        )
    )
    instance = None
    report: dict[str, Any] = {
        "passed": False,
        "scope": "automated acceptance fixture records local-user decisions for real model trials; no human review or activation claim",
    }
    try:
        instance = await provider.load_model("qwen/qwen3.5-9b", context_length=32768)
        trials: list[TrialReview] = []
        outcomes: list[dict[str, Any]] = []
        for case in ("ASCII single line", "Unicode multiline"):
            workspace = root / ("ascii" if case.startswith("ASCII") else "unicode")
            workspace.mkdir()
            code = "TRIAL-CODE-" + uuid4().hex
            original = (
                (code + "\n")
                if case.startswith("ASCII")
                else (
                    "R\u00e9sum\u00e9 \u3053\u3093\u306b\u3061\u306f\nCode: "
                    + code
                    + "\nEnd of fixture.\n"
                )
            )
            fixture = workspace / "input.txt"
            fixture.write_bytes(original.encode("utf-8"))
            raw, stamp = fixture.read_bytes(), fixture.stat().st_mtime_ns
            run = store.create_run(
                objective="Read input.txt and report the exact TRIAL-CODE.",
                acceptance_criteria=["Correct code", "Procedure reporting label"],
                workspace=str(workspace),
                mode="observe",
                model=instance,
                max_iterations=4,
                max_seconds=90,
                max_output_tokens=1024,
                max_total_tokens=20000,
                file_checks=[{"path": "input.txt", "expected_content": original}],
                procedure_trial={"skill_id": skill["id"], "version": 1},
            )
            await Coordinator(store, provider).execute(run["id"])
            saved = store.get_run(run["id"])
            assert saved is not None
            final = store.conversation_messages(run["id"])[-1].get("content") or ""
            assert saved["state"] == "verifying" and code in final and label in final
            assert fixture.read_bytes() == raw and fixture.stat().st_mtime_ns == stamp
            await review_run(
                store,
                run["id"],
                RunReviewDecision(
                    decision="accept",
                    expected_revision=saved["revision"],
                    objective_confirmed=True,
                    confirmed_criteria=[0, 1],
                    rationale="Automated acceptance harness checked exact file code, procedure-only label and unchanged fixture bytes/mtime. This is a test review, not a human review claim.",
                ),
            )
            trials.append(TrialReview(run_id=run["id"], case_description=case))
            outcomes.append(
                {
                    "run_id": run["id"],
                    "case": case,
                    "exact_code_and_procedure_label": True,
                    "fixture_unchanged": True,
                    "automated_final_review": True,
                }
            )
        evaluated = skills.evaluate(
            skill["id"],
            1,
            SkillEvaluation(
                expected_evaluation_count=0,
                trials=trials,
                criteria=[
                    "Correct code and procedure-only reporting label",
                    "Fixture preserved across both representative inputs",
                ],
                confirmed_criteria=[0, 1],
                rationale="Automated fixture checks passed for single-line ASCII and multiline Unicode inputs; record this explicit test review without activating the procedure.",
            ),
        )
        summary = skills.get(skill["id"])
        assert summary["lifecycle_state"] == "evaluated" and summary["active_version"] is None
        assert len(evaluated["evaluation_evidence"]["evaluations"]) == 1
        report.update(
            passed=True,
            skill_id=skill["id"],
            version=1,
            lifecycle_state=summary["lifecycle_state"],
            active_version=None,
            trials=outcomes,
            evaluation_count=1,
        )
    finally:
        report["counts"] = provider.rows
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

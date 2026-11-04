"""Real-model evaluated-version upgrade, active reuse, rollback and deprecation acceptance."""

import argparse
import asyncio
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from canonical_context_diagnostic import AuditedProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.run_review import RunReviewDecision, review_run
from xaa.storage.runs import RunStore
from xaa.storage.skills import (
    SkillEvaluation,
    SkillLifecycleDecision,
    SkillRevision,
    SkillStore,
    TrialReview,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    source = parser.parse_args().snapshot.resolve()
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    with (
        sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as src,
        sqlite3.connect(root / "runs.sqlite3") as dst,
    ):
        src.backup(dst)
    store = RunStore(root / "runs.sqlite3")
    skills = SkillStore(store)
    skill = skills.list()[0]
    if skill["latest_version"] != 1 or skill["active_version"] is not None:
        raise ValueError("Source snapshot must contain an inactive evaluated version 1.")
    original = skills.version(skill["id"], 1)
    label_match = re.search(r"EVALUATION-LABEL-[a-f0-9]+", original["content"])
    assert label_match is not None
    label = label_match.group()
    v2_marker = "LIFECYCLE-V2-" + uuid4().hex
    provider = AuditedProvider()
    instance = None
    outcomes: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "passed": False,
        "scope": "automated isolated acceptance using real-model trial evidence; no human review claim",
    }

    def decide(action: str, target: int | None = None) -> None:
        summary = skills.get(skill["id"])
        skills.decide_lifecycle(
            skill["id"],
            SkillLifecycleDecision.model_validate(
                {
                    "action": action,
                    "target_version": target,
                    "expected_active_version": summary["active_version"],
                    "expected_latest_version": summary["latest_version"],
                    "expected_lifecycle_revision": summary["lifecycle_revision"],
                    "rationale": "Automated acceptance fixture explicitly selects this evaluated version; no human review claim.",
                }
            ),
        )

    async def run_case(case: str, version: int, purpose: str, unicode_input: bool = False) -> str:
        workspace = root / case
        workspace.mkdir()
        code = "TRIAL-CODE-" + uuid4().hex
        text = (
            ("R\u00e9sum\u00e9 \u3053\u3093\u306b\u3061\u306f\n" if unicode_input else "")
            + code
            + "\n"
        )
        fixture = workspace / "input.txt"
        fixture.write_bytes(text.encode("utf-8"))
        stamp = fixture.stat().st_mtime_ns
        run = store.create_run(
            objective="Read input.txt and report its exact TRIAL-CODE.",
            acceptance_criteria=["Exact code and procedure version reporting format"],
            workspace=str(workspace),
            mode="observe",
            model=instance,
            max_iterations=4,
            max_seconds=90,
            max_output_tokens=1024,
            max_total_tokens=20000,
            file_checks=[{"path": "input.txt", "expected_content": text}],
            procedure_trial={"skill_id": skill["id"], "version": version, "purpose": purpose},
        )
        await Coordinator(store, provider).execute(run["id"])
        saved = store.get_run(run["id"])
        assert saved is not None
        final = store.conversation_messages(run["id"])[-1].get("content") or ""
        assert saved["state"] == "verifying" and code in final and label in final
        assert (v2_marker in final) == (version == 2)
        assert fixture.read_bytes() == text.encode("utf-8") and fixture.stat().st_mtime_ns == stamp
        if purpose == "evaluation_trial":
            await review_run(
                store,
                run["id"],
                RunReviewDecision(
                    decision="accept",
                    expected_revision=saved["revision"],
                    objective_confirmed=True,
                    confirmed_criteria=[0],
                    rationale="Automated fixture confirmed exact random code, version-specific labels and unchanged fixture.",
                ),
            )
        outcomes.append(
            {
                "case": case,
                "run_id": run["id"],
                "version": version,
                "purpose": purpose,
                "exact_code_and_version_format": True,
                "fixture_unchanged": True,
            }
        )
        return run["id"]

    try:
        instance = await provider.load_model("qwen/qwen3.5-9b", context_length=32768)
        decide("activate", 1)
        skills.revise(
            skill["id"],
            SkillRevision.model_validate(
                {
                    "name": skill["name"],
                    "content": original["content"]
                    + " Also include this exact additional marker in the final answer: "
                    + v2_marker,
                    "required_tools": original["required_tools"],
                    "required_permissions": original["required_permissions"],
                    "expected_version": 1,
                }
            ),
        )
        first = await run_case("v2-ascii-trial", 2, "evaluation_trial")
        second = await run_case("v2-unicode-trial", 2, "evaluation_trial", True)
        skills.evaluate(
            skill["id"],
            2,
            SkillEvaluation(
                expected_evaluation_count=0,
                trials=[
                    TrialReview(run_id=first, case_description="ASCII v2 reporting"),
                    TrialReview(run_id=second, case_description="Unicode v2 reporting"),
                ],
                criteria=["Exact evidence and version-two marker without file edits"],
                confirmed_criteria=[0],
                rationale="Automated acceptance verified both version-two representative cases.",
            ),
        )
        assert skills.get(skill["id"])["active_version"] == 1
        decide("activate", 2)
        await run_case("active-v2", 2, "active_use", True)
        decide("rollback", 1)
        await run_case("rolled-back-v1", 1, "active_use")
        decide("deprecate")
        try:
            store.create_run(
                objective="Rejected deprecated selection",
                acceptance_criteria=[],
                workspace=str(root),
                mode="observe",
                model=instance,
                procedure_trial={"skill_id": skill["id"], "version": 1, "purpose": "active_use"},
            )
        except ValueError:
            pass
        else:
            raise RuntimeError("Deprecated procedure was selectable")
        actions = skills.lifecycle(skill["id"])
        assert [entry["action"] for entry in actions] == [
            "activate",
            "activate",
            "rollback",
            "deprecate",
        ]
        report.update(
            passed=True,
            skill_id=skill["id"],
            outcomes=outcomes,
            lifecycle_actions=[entry["action"] for entry in actions],
            final_state=skills.get(skill["id"])["lifecycle_state"],
            active_version=skills.get(skill["id"])["active_version"],
            deprecated_new_selection_rejected=True,
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

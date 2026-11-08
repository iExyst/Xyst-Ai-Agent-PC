"""Verify a real model can atomically replace and remove procedure resources."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore
from xaa.storage.skills import SkillAsset, SkillDraft, SkillStore


async def _run(model_id: str, report_path: Path | None) -> dict[str, Any]:
    root = Path(".acceptance") / f"live-procedure-resource-mixed-{uuid4()}"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    data = root / "data"
    data.mkdir()
    obsolete = b"Obsolete reference; remove this file."
    original = b"Old guide that should be replaced."
    replacement = b"Updated guide: preserve checksums and cite sources."
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance: str | None = None
    model_context_length = 16_384
    try:
        models = await provider.models()
        selected_model = next((model for model in models if model.id == model_id), None)
        if selected_model is None or not selected_model.capabilities.trained_for_tool_use:
            raise RuntimeError(f"Selected tool-capable model was not discovered: {model_id}")
        instance = await provider.load_model(model_id, context_length=model_context_length)

        draft = SkillDraft(
            name="Live mixed resource edit",
            description="Synthetic fixture for linked-resource batch acceptance.",
            category="testing",
            content="Read the selected linked resources and report their paths.",
            required_tools=[],
            required_permissions=[],
            assets=[
                SkillAsset(
                    path="references/guide.md",
                    media_type="text/markdown",
                    sha256=hashlib.sha256(original).hexdigest(),
                    content_base64=base64.b64encode(original).decode("ascii"),
                ),
                SkillAsset(
                    path="references/obsolete.md",
                    media_type="text/markdown",
                    sha256=hashlib.sha256(obsolete).hexdigest(),
                    content_base64=base64.b64encode(obsolete).decode("ascii"),
                ),
            ],
        )
        store = RunStore(data / "xaa.sqlite3")
        skills = SkillStore(store)
        procedure = skills.create(draft)
        before = skills.version(procedure["id"], 1)
        run = store.create_run(
            objective=(
                "Use procedure_view and procedure_read_resource to inspect the selected package. "
                "Then make one atomic linked-resource edit: replace references/guide.md with "
                "exactly `Updated guide: preserve checksums and cite sources.` and remove "
                "references/obsolete.md using the combined procedure_resource_revise operation. "
                "Do not create or edit workspace files. Do not activate or evaluate the candidate."
            ),
            acceptance_criteria=[
                "The new candidate contains the exact replacement guide and no obsolete reference.",
                "The original version remains unchanged and the candidate stays inactive.",
            ],
            workspace=str(workspace.resolve()),
            mode="observe",
            model=model_id,
            procedure_trial={"skill_id": procedure["id"], "version": 1},
            allow_procedure_resource_revision=True,
            allow_procedure_resource_removal=True,
            max_iterations=8,
            max_seconds=300,
        )
        run_id = str(run["id"])
        await Coordinator(store, provider).execute(run_id)

        latest = skills.get(procedure["id"])["latest_version"]
        after = skills.version(procedure["id"], latest) if latest > 1 else None
        with store.connection() as connection:
            tool_rows = connection.execute(
                "SELECT tool_name,state,result FROM tool_calls WHERE run_id=? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        operations: list[dict[str, Any]] = []
        for row in tool_rows:
            if row["tool_name"] == "procedure.resource_revise" and row["result"]:
                payload = json.loads(row["result"])
                operations = payload.get("resources", [])
        saved_run = store.get_run(run_id)
        read_calls = [
            {"tool": str(row["tool_name"]), "state": str(row["state"])} for row in tool_rows
        ]
        assets = {asset["path"]: asset for asset in after["assets"]} if after else {}
        replacement_matches = bool(
            "references/guide.md" in assets
            and base64.b64decode(assets["references/guide.md"]["content_base64"]) == replacement
        )
        passed = bool(
            saved_run
            and saved_run["state"] == "verifying"
            and replacement_matches
            and "references/obsolete.md" not in assets
            and skills.version(procedure["id"], 1) == before
            and after is not None
            and after["evaluation_evidence"]["state"] == "candidate"
            and skills.get(procedure["id"])["active_version"] is None
            and any(
                row["tool_name"] == "procedure.resource_revise" and row["state"] == "completed"
                for row in tool_rows
            )
            and not store.unresolved_tool_calls(run_id)
            and not list(workspace.iterdir())
        )
        report: dict[str, Any] = {
            "passed": passed,
            "verified_at": datetime.now(UTC).isoformat(),
            "model": model_id,
            "provider": "LM Studio local API",
            "model_context_length": model_context_length,
            "run_state": saved_run["state"] if saved_run else None,
            "candidate_version": after["version"] if after else None,
            "tool_calls": read_calls,
            "resource_operations": operations,
            "replacement_bytes_match": replacement_matches,
            "removed_path_absent": "references/obsolete.md" not in assets,
            "original_version_unchanged": skills.version(procedure["id"], 1) == before,
            "candidate_inactive_and_unevaluated": (
                after is not None
                and after["evaluation_evidence"]["state"] == "candidate"
                and skills.get(procedure["id"])["active_version"] is None
            ),
            "unresolved_tool_calls": len(store.unresolved_tool_calls(run_id)),
            "workspace_files": sorted(path.name for path in workspace.iterdir()),
            "local_inference_only": True,
            "user_project_modified": False,
        }
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        if not passed:
            raise RuntimeError(
                f"Live mixed-resource acceptance failed; inspect {root / 'report.json'}."
            )
        if report_path is not None:
            destination = report_path.resolve()
            if destination.exists():
                raise FileExistsError(f"Refusing to overwrite existing evidence: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        if instance is not None:
            await provider.unload_model(instance)
        await provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen/qwen3.5-9b")
    parser.add_argument("--report", type=Path, help="Optional new JSON acceptance report path")
    args = parser.parse_args()
    asyncio.run(_run(args.model, args.report))


if __name__ == "__main__":
    main()

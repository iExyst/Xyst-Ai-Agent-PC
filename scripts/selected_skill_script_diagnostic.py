"""Run one selected Python skill resource through the granted isolated WSL command path."""

import argparse
import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from xaa.runtime.command_controller import CommandController
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.runtime.wsl_transport import WSLTransport, profile_fingerprint
from xaa.storage.runs import RunStore
from xaa.storage.skills import SkillAsset, SkillDraft, SkillStore


async def diagnose(distribution: str, report_root: Path) -> Path:
    root = report_root.resolve() / str(uuid4())
    project = root / "project"
    project.mkdir(parents=True)
    marker = uuid4().hex
    (project / "fixture.txt").write_text(marker, encoding="utf-8")
    original_snapshot = capture_workspace(project)

    store = RunStore(root / "app-data" / "XAA" / "xaa.sqlite3")
    script = b"import pathlib; print(pathlib.Path('fixture.txt').read_text())\n"
    skill = SkillStore(store).create(
        SkillDraft(
            name="Selected script execution diagnostic",
            content="Read fixture.txt and print its contents.",
            required_tools=[],
            required_permissions=["workspace.read"],
            assets=[
                SkillAsset(
                    path="scripts/check.py",
                    content_base64=base64.b64encode(script).decode("ascii"),
                    media_type="text/x-python",
                    sha256=hashlib.sha256(script).hexdigest(),
                )
            ],
        )
    )
    grant_fingerprint = profile_fingerprint()[0]
    run = store.create_run(
        objective="Execute a selected Python skill resource in the private command snapshot.",
        acceptance_criteria=["The isolated script prints the fixture marker."],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": distribution, "profile_sha256": grant_fingerprint},
        procedure_trial={"skill_id": skill["id"], "version": 1},
    )
    owner = store.acquire_lease(run["id"], "selected-skill-script-diagnostic", seconds=90)
    if owner is None:
        raise RuntimeError("Could not acquire diagnostic run lease.")
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)

    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
    script_path = f"__xaa_selected_skill__/{skill['id']}/v1/scripts/check.py"
    result = await CommandController(store, transport).execute(
        run["id"],
        owner,
        "selected-skill-script",
        {"argv": ["/usr/bin/python3", "-I", script_path]},
        idempotency_key="selected-skill-script",
        deadline=time.monotonic() + 35,
    )
    if not result.get("succeeded") or marker not in str(result.get("stdout_excerpt", "")):
        raise AssertionError("Selected script did not return the project fixture marker.")
    if capture_workspace(project).sha256 != original_snapshot.sha256:
        raise AssertionError("Restricted script execution modified the original project.")
    if store.unresolved_tool_calls(run["id"]):
        raise AssertionError("Selected script command remains unresolved after completion.")

    store.transition(run["id"], RunState.CANCELLED, reason="diagnostic_complete")
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": True,
        "distribution": distribution,
        "profile_sha256": grant_fingerprint,
        "selected_script_path": script_path,
        "command_succeeded": True,
        "script_read_project_fixture": True,
        "original_project_unchanged": True,
        "unresolved_commands": 0,
        "run_state": "cancelled_after_diagnostic",
        "model_inference": False,
        "scope": "One selected Python resource executed through the saved WSL grant and private snapshot.",
    }
    report_path = root / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Selected skill script execution passed: {report_path}")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument(
        "--report-root", type=Path, default=Path(".acceptance/selected-skill-script")
    )
    args = parser.parse_args()
    asyncio.run(diagnose(args.distribution, args.report_root))


if __name__ == "__main__":
    main()

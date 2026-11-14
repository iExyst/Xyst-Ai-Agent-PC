"""Execute selected Python, shell and JavaScript resources through a specialist task."""

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
from xaa.runtime.task_commands import command_evidence
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_worker import execute_filesystem
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.runtime.wsl_transport import WSLTransport, profile_fingerprint
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.skills import SkillAsset, SkillDraft, SkillStore
from xaa.storage.tasks import TaskStore


async def diagnose(distribution: str, report_root: Path) -> Path:
    root = report_root.resolve() / str(uuid4())
    project = root / "project"
    project.mkdir(parents=True)
    marker = uuid4().hex
    (project / "fixture.txt").write_text(marker, encoding="utf-8")
    original_snapshot = capture_workspace(project)

    store = RunStore(root / "app-data" / "XAA" / "xaa.sqlite3")
    python_script = b"import pathlib; print(pathlib.Path('fixture.txt').read_text())\n"
    shell_script = b"#!/usr/bin/env bash\ncat fixture.txt\n"
    node_script = (
        b"import { readFileSync } from 'node:fs';\n"
        b"console.log(readFileSync('fixture.txt', 'utf8'));\n"
    )
    skill = SkillStore(store).create(
        SkillDraft(
            name="Selected specialist script diagnostic",
            content="Read fixture.txt and print its contents.",
            required_tools=[],
            required_permissions=["workspace.read"],
            assets=[
                SkillAsset(
                    path="scripts/check.py",
                    content_base64=base64.b64encode(python_script).decode("ascii"),
                    media_type="text/x-python",
                    sha256=hashlib.sha256(python_script).hexdigest(),
                ),
                SkillAsset(
                    path="scripts/check.sh",
                    content_base64=base64.b64encode(shell_script).decode("ascii"),
                    media_type="text/x-shellscript",
                    sha256=hashlib.sha256(shell_script).hexdigest(),
                ),
                SkillAsset(
                    path="scripts/check.mjs",
                    content_base64=base64.b64encode(node_script).decode("ascii"),
                    media_type="text/javascript",
                    sha256=hashlib.sha256(node_script).hexdigest(),
                ),
            ],
        )
    )
    profile_sha256 = profile_fingerprint()[0]
    run = store.create_run(
        objective="Execute selected Python, shell and Node.js resources from a granted specialist task.",
        acceptance_criteria=["Each isolated specialist script prints the fixture marker."],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": distribution, "profile_sha256": profile_sha256},
        procedure_trial={"skill_id": skill["id"], "version": 1},
    )
    run_id = run["id"]
    owner = store.acquire_lease(run_id, "selected-skill-specialist-diagnostic", seconds=180)
    if owner is None:
        raise RuntimeError("Could not acquire diagnostic run lease.")
    store.transition(run_id, RunState.PLANNING, owner=owner)
    store.transition(run_id, RunState.RUNNING, owner=owner)

    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id="script-check",
                objective="Run the selected Python check script against the assigned fixture.",
                acceptance_criteria=("Print the fixture marker.",),
                permissions=("workspace.read", "workspace.write", "workspace.command"),
            )
        ],
    )
    claim = tasks.claim_ready(run_id, owner)
    if claim is None:
        raise RuntimeError("Could not claim the diagnostic specialist task.")
    scope = TaskReadScope(run_id, owner, "script-check", claim["token"])
    await execute_filesystem(
        store,
        run_id,
        owner,
        "initialize-private-workspace",
        "branch_init",
        {},
        idempotency_key="initialize-private-workspace",
        internal=True,
        task_id=scope.task_id,
        task_token=scope.task_token,
    )

    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
    python_path = f"__xaa_selected_skill__/{skill['id']}/v1/scripts/check.py"
    shell_path = f"__xaa_selected_skill__/{skill['id']}/v1/scripts/check.sh"
    node_path = f"__xaa_selected_skill__/{skill['id']}/v1/scripts/check.mjs"
    controller = CommandController(store, transport, task_scope=scope)
    commands_succeeded = 0
    for call_id, argv in [
        ("selected-specialist-python-script", ["/usr/bin/python3", "-I", python_path]),
        ("selected-specialist-shell-script", ["/bin/bash", shell_path]),
        ("selected-specialist-node-script", ["/usr/bin/node", "--no-addons", node_path]),
    ]:
        result = await controller.execute(
            run_id,
            owner,
            call_id,
            {"argv": argv},
            idempotency_key=call_id,
            deadline=time.monotonic() + 35,
        )
        if not result.get("succeeded") or marker not in str(result.get("stdout_excerpt", "")):
            raise AssertionError(f"Specialist script {argv[-1]} did not return the fixture marker.")
        commands_succeeded += 1
    evidence = command_evidence(store, run_id, scope.task_id)
    if len(evidence) != 3 or not all(item["matches_current_private_tree"] for item in evidence):
        raise AssertionError("Specialist command evidence does not match its augmented snapshot.")
    if capture_workspace(project).sha256 != original_snapshot.sha256:
        raise AssertionError("Restricted specialist execution modified the original project.")
    if store.unresolved_tool_calls(run_id):
        raise AssertionError("Selected specialist script command remains unresolved.")

    store.transition(run_id, RunState.CANCELLED, reason="diagnostic_complete")
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": True,
        "distribution": distribution,
        "profile_sha256": profile_sha256,
        "selected_script_paths": [python_path, shell_path, node_path],
        "task_id": scope.task_id,
        "task_permission_scope": ["workspace.read", "workspace.write", "workspace.command"],
        "commands_succeeded": commands_succeeded,
        "python_shell_and_node_scripts_read_private_project_fixture": True,
        "command_evidence_matches_current_augmented_snapshot": True,
        "original_project_unchanged": True,
        "unresolved_commands": 0,
        "run_state": "cancelled_after_diagnostic",
        "model_inference": False,
        "scope": "Selected Python, shell and Node.js resources execute through a specialist private snapshot with saved WSL and task command grants; npm dependencies are not installed.",
    }
    report_path = root / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Selected specialist skill script execution passed: {report_path}")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument(
        "--report-root", type=Path, default=Path(".acceptance/selected-skill-specialist")
    )
    args = parser.parse_args()
    asyncio.run(diagnose(args.distribution, args.report_root))


if __name__ == "__main__":
    main()

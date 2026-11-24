"""Run selected Python, JavaScript, and PowerShell resources through restricted WSL."""

import argparse
import asyncio
import base64
import hashlib
import json
import re
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
    node_script = (
        b"import { readFileSync } from 'node:fs';\n"
        b"console.log(readFileSync('fixture.txt', 'utf8'));\n"
    )
    powershell_script = (
        b"$passwd = (Get-Content /etc/passwd -Raw).Trim()\n"
        b"$group = (Get-Content /etc/group -Raw).Trim()\n"
        b"if ($passwd -ne 'nobody:x:65534:65534:nobody:/tmp:/usr/sbin/nologin') { exit 31 }\n"
        b"if ($group -ne 'nogroup:x:65534:') { exit 32 }\n"
        b"Get-Content fixture.txt\n"
        b'Write-Output "PS_VERSION=$($PSVersionTable.PSVersion.ToString())"\n'
    )
    skill = SkillStore(store).create(
        SkillDraft(
            name="Selected script execution diagnostic",
            content="Read fixture.txt and print its contents using Python and Node.js.",
            required_tools=[],
            required_permissions=["workspace.read"],
            assets=[
                SkillAsset(
                    path="scripts/check.py",
                    content_base64=base64.b64encode(script).decode("ascii"),
                    media_type="text/x-python",
                    sha256=hashlib.sha256(script).hexdigest(),
                ),
                SkillAsset(
                    path="scripts/check.mjs",
                    content_base64=base64.b64encode(node_script).decode("ascii"),
                    media_type="text/javascript",
                    sha256=hashlib.sha256(node_script).hexdigest(),
                ),
                SkillAsset(
                    path="scripts/check.ps1",
                    content_base64=base64.b64encode(powershell_script).decode("ascii"),
                    media_type="text/plain",
                    sha256=hashlib.sha256(powershell_script).hexdigest(),
                ),
            ],
        )
    )
    grant_fingerprint = profile_fingerprint()[0]
    run = store.create_run(
        objective="Execute selected Python and Node.js skill resources in the private command snapshot.",
        acceptance_criteria=["Both isolated scripts print the fixture marker."],
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
        raise AssertionError("Selected Python script did not return the project fixture marker.")
    node_script_path = f"__xaa_selected_skill__/{skill['id']}/v1/scripts/check.mjs"
    node_result = await CommandController(store, transport).execute(
        run["id"],
        owner,
        "selected-skill-node-script",
        {"argv": ["/usr/bin/node", "--no-addons", node_script_path]},
        idempotency_key="selected-skill-node-script",
        deadline=time.monotonic() + 35,
    )
    if not node_result.get("succeeded") or marker not in str(node_result.get("stdout_excerpt", "")):
        raise AssertionError("Selected Node.js script did not return the project fixture marker.")
    powershell_script_path = f"__xaa_selected_skill__/{skill['id']}/v1/scripts/check.ps1"
    powershell_result = await CommandController(store, transport).execute(
        run["id"],
        owner,
        "selected-skill-powershell-script",
        {
            "argv": [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                powershell_script_path,
            ]
        },
        idempotency_key="selected-skill-powershell-script",
        deadline=time.monotonic() + 35,
    )
    if not powershell_result.get("succeeded") or marker not in str(
        powershell_result.get("stdout_excerpt", "")
    ):
        raise AssertionError(
            "Selected PowerShell script did not return the project fixture marker: "
            f"{powershell_result!r}"
        )
    powershell_output = str(powershell_result.get("stdout_excerpt", ""))
    version_match = re.search(r"(?m)^PS_VERSION=(\d+\.\d+\.\d+)\s*$", powershell_output)
    if version_match is None:
        raise AssertionError("Selected PowerShell script did not report its runtime version.")
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
        "selected_script_paths": [script_path, node_script_path, powershell_script_path],
        "command_succeeded": True,
        "python_script_read_project_fixture": True,
        "node_script_read_project_fixture": True,
        "powershell_script_read_project_fixture": True,
        "powershell_received_only_synthetic_sandbox_identity": True,
        "powershell_version": version_match.group(1),
        "original_project_unchanged": True,
        "unresolved_commands": 0,
        "run_state": "cancelled_after_diagnostic",
        "model_inference": False,
        "scope": "Selected Python, Node.js, and PowerShell resources execute through the saved WSL grant and private snapshot; npm dependencies are not installed.",
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

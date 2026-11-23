"""Verify explicit specialist credential grants reach WSL without output disclosure."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.tasks import TaskStore

OUTPUT_MARKER = b"[command output withheld because procedure credentials were authorized]"
FIXTURE_SECRET = "xaa-wsl-specialist-credential-fixture-6a9f31"
PROCEDURE = b"""import base64, hashlib, os, sys
secret = os.environ['API_TOKEN']
assert hashlib.sha256(secret.encode()).hexdigest() == sys.argv[1]
print(secret)
print(base64.b64encode(secret.encode()).decode())
print('stderr:' + secret, file=sys.stderr)
"""


async def run(distribution: str) -> dict[str, Any]:
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
    with tempfile.TemporaryDirectory(prefix="xaa-specialist-credential-") as temporary:
        root = Path(temporary)
        data_root = root / "data"
        data_root.mkdir()
        workspace = root / "workspace"
        workspace.mkdir()
        store = RunStore(data_root / "runs.sqlite3")
        skill_id = str(uuid4())
        now = "2026-01-01T00:00:00+00:00"
        declared = json.dumps({"required_environment_names": ["API_TOKEN"]})
        evidence = json.dumps({"source": {"declared_requirements": declared}})
        script_path = f"__xaa_selected_skill__/{skill_id}/v1/scripts/check.py"
        with store.connection() as connection:
            connection.execute(
                "INSERT INTO skills(id,name,lifecycle_state,active_version,created_at,updated_at) "
                "VALUES(?, 'WSL specialist credential acceptance', 'active', 1, ?, ?)",
                (skill_id, now, now),
            )
            connection.execute(
                "INSERT INTO skill_versions(skill_id,version,content,required_tools,required_permissions,evaluation_evidence,created_at) "
                "VALUES(?,1,'Run the selected credential check script.','[]','[]',?,?)",
                (skill_id, evidence, now),
            )
            connection.execute(
                "INSERT INTO skill_version_assets(skill_id,version,relative_path,media_type,size_bytes,sha256,content) "
                "VALUES(?,1,'scripts/check.py','text/x-python',?,?,?)",
                (skill_id, len(PROCEDURE), hashlib.sha256(PROCEDURE).hexdigest(), PROCEDURE),
            )
        SpecialistProfileStore(store).set_procedure("executor", skill_id)
        run = store.create_run(
            objective="Verify a specialist's owner-granted procedure credential",
            acceptance_criteria=[],
            workspace=str(workspace),
            mode="workspace",
            model=None,
            command_grant={
                "backend": "wsl-bubblewrap-v1",
                "distribution": distribution,
                "profile_sha256": transport.profile_sha256,
                "max_seconds": 20,
            },
        )
        run_id = str(run["id"])
        with store.connection() as connection:
            store.record_event(
                connection,
                run_id,
                "specialist.procedure_credentials_granted",
                {
                    "grants": [
                        {
                            "role": "executor",
                            "skill_id": skill_id,
                            "version": 1,
                            "credential_names": ["API_TOKEN"],
                        }
                    ]
                },
            )
        owner = store.acquire_lease(run_id, "wsl-specialist-credential-acceptance")
        if owner is None:
            raise RuntimeError(
                "The specialist credential acceptance could not acquire its run lease."
            )
        store.transition(run_id, RunState.PLANNING, owner=owner)
        store.transition(run_id, RunState.RUNNING, owner=owner)
        tasks = TaskStore(store)
        tasks.save_plan(
            run_id,
            owner,
            [
                TaskDefinition(
                    id="execute",
                    objective="Run the selected specialist procedure script",
                    role="executor",
                    permissions=("workspace.read", "workspace.write", "workspace.command"),
                )
            ],
        )
        task = tasks.records(run_id)[0]
        specialist_snapshot = task["procedure_snapshot"]
        if specialist_snapshot.get("credential_names") != [
            "API_TOKEN"
        ] or FIXTURE_SECRET in json.dumps(specialist_snapshot):
            raise RuntimeError(
                "The specialist snapshot did not preserve only the granted credential name."
            )
        claim = tasks.claim_ready(run_id, owner)
        if claim is None:
            raise RuntimeError("The credential acceptance specialist task was not claimable.")
        journal = CommandJournal(
            store,
            task_scope=TaskReadScope(run_id, owner, "execute", str(claim["token"])),
        )
        intent = journal.reserve(
            run_id,
            owner,
            "specialist-credential-acceptance",
            {
                "argv": [
                    "python3",
                    script_path,
                    hashlib.sha256(FIXTURE_SECRET.encode()).hexdigest(),
                ],
                "max_seconds": 10,
            },
            idempotency_key="specialist-credential-acceptance",
        )
        if intent.procedure_credential_names != ["API_TOKEN"]:
            raise RuntimeError("The specialist command intent lost its exact credential names.")
        snapshot = WorkspaceSnapshot(
            files=(SnapshotFile(script_path, PROCEDURE),),
            directories=(
                "__xaa_selected_skill__",
                f"__xaa_selected_skill__/{skill_id}",
                f"__xaa_selected_skill__/{skill_id}/v1",
                f"__xaa_selected_skill__/{skill_id}/v1/scripts",
            ),
        )
        result = await transport.run(
            intent,
            snapshot,
            deadline=time.monotonic() + 30,
            procedure_secrets={"API_TOKEN": FIXTURE_SECRET},
        )

    stdout = base64.b64decode(result.get("stdout_b64", ""), validate=True)
    stderr = base64.b64decode(result.get("stderr_b64", ""), validate=True)
    encoded_secret = base64.b64encode(FIXTURE_SECRET.encode()).decode()
    if (
        result.get("succeeded") is not True
        or result.get("exit_code") != 0
        or result.get("cleanup_confirmed") is not True
        or stdout != OUTPUT_MARKER
        or stderr != OUTPUT_MARKER
        or FIXTURE_SECRET.encode() in stdout + stderr
        or encoded_secret.encode() in stdout + stderr
        or FIXTURE_SECRET in json.dumps(result)
        or encoded_secret in json.dumps(result)
    ):
        raise RuntimeError("Owner-granted WSL specialist credential acceptance failed.")
    return {
        "distribution": distribution,
        "role": "executor",
        "run_scoped_snapshot_name_only": True,
        "run_granted_environment_available": True,
        "command_output_withheld": stdout == OUTPUT_MARKER and stderr == OUTPUT_MARKER,
        "direct_secret_absent": FIXTURE_SECRET not in json.dumps(result),
        "encoded_secret_absent": encoded_secret not in json.dumps(result),
        "cleanup_confirmed": True,
        "live_model_inference_requested": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args.distribution))
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()

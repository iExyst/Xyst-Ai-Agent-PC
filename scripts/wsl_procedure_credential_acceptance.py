"""Verify run-granted procedure credentials reach WSL without exposing command output."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport

OUTPUT_MARKER = b"[command output withheld because procedure credentials were authorized]"
FIXTURE_SECRET = "xaa-wsl-credential-fixture-5f2a9c"

PROCEDURE = b"""import base64, hashlib, os, sys
secret = os.environ['API_TOKEN']
assert hashlib.sha256(secret.encode()).hexdigest() == sys.argv[1]
print(secret)
print(base64.b64encode(secret.encode()).decode())
print('stderr:' + secret, file=sys.stderr)
"""


async def run(distribution: str) -> dict[str, Any]:
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
    skill_id = str(uuid4())
    script = f"__xaa_selected_skill__/{skill_id}/v1/scripts/check.py"
    snapshot = WorkspaceSnapshot(
        files=(SnapshotFile(script, PROCEDURE),),
        directories=(
            "__xaa_selected_skill__",
            f"__xaa_selected_skill__/{skill_id}",
            f"__xaa_selected_skill__/{skill_id}/v1",
            f"__xaa_selected_skill__/{skill_id}/v1/scripts",
        ),
    )
    intent = CommandIntent(
        argv=["python3", script, hashlib.sha256(FIXTURE_SECRET.encode()).hexdigest()],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="wsl-bubblewrap-v1",
        distribution=distribution,
        profile_sha256=transport.profile_sha256,
        workspace="application-owned-procedure-credential-acceptance",
        procedure_skill_id=skill_id,
        procedure_version=1,
        procedure_credential_names=["API_TOKEN"],
    )
    result = await transport.run(
        intent,
        snapshot,
        deadline=time.monotonic() + 30,
        procedure_secrets={"API_TOKEN": FIXTURE_SECRET},
    )
    stdout = base64.b64decode(result.get("stdout_b64", ""), validate=True)
    stderr = base64.b64decode(result.get("stderr_b64", ""), validate=True)
    if (
        result.get("succeeded") is not True
        or result.get("exit_code") != 0
        or result.get("cleanup_confirmed") is not True
        or stdout != OUTPUT_MARKER
        or stderr != OUTPUT_MARKER
        or FIXTURE_SECRET.encode() in stdout + stderr
        or base64.b64encode(FIXTURE_SECRET.encode()) in stdout + stderr
        or FIXTURE_SECRET in json.dumps(result)
    ):
        raise RuntimeError("Owner-granted WSL procedure credential acceptance failed.")
    return {
        "distribution": distribution,
        "run_granted_environment_available": True,
        "command_output_withheld": stdout == OUTPUT_MARKER and stderr == OUTPUT_MARKER,
        "direct_secret_absent": FIXTURE_SECRET not in json.dumps(result),
        "encoded_secret_absent": base64.b64encode(FIXTURE_SECRET.encode()).decode()
        not in json.dumps(result),
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

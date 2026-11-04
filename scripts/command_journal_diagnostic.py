"""Actual WSL execution bound to Windows SQLite grants, leases and output evidence."""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.executor import ReconciliationRequired
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.storage.commands import CommandCleanupEvidence, CommandJournal, CommandOutcome
from xaa.storage.runs import LeaseLost, RunStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    args = parser.parse_args()
    if os.name != "nt" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.distribution):
        raise ValueError("Choose an existing WSL distribution on Windows.")
    root = Path(".acceptance") / str(uuid4())
    project = root / "project"
    project.mkdir(parents=True)
    nonce = uuid4().hex
    (project / "fixture.txt").write_text(nonce, encoding="utf-8")
    modules = [
        Path("engine/xaa/runtime") / name
        for name in (
            "wsl_manager.py",
            "workspace_snapshot.py",
            "sandbox_profile.py",
            "execution_receipts.py",
            "cgroup_gate.py",
        )
    ]
    fingerprints = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in modules}
    profile_digest = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    prefix = [
        str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe"),
        "--distribution",
        args.distribution,
        "--exec",
    ]
    paths = [
        subprocess.run(
            [*prefix, "/usr/bin/wslpath", "-a", "-u", str(path.resolve())],
            check=True,
            capture_output=True,
            timeout=10,
            creationflags=0x08000000,
        )
        .stdout.decode()
        .strip()
        for path in modules
    ]
    invocation = [*prefix, "/usr/bin/python3", "-I", *paths[:4]]
    store = RunStore(root / "data" / "runs.sqlite3")
    run = store.create_run(
        objective="Exercise journaled private command execution",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={
            "distribution": args.distribution,
            "profile_sha256": profile_digest,
            "max_seconds": 30,
        },
    )
    run_id = run["id"]

    def acquire_owner(label: str) -> str:
        token = store.acquire_lease(run_id, label, seconds=3600)
        if token is None:
            raise RuntimeError("Diagnostic could not acquire its owned run lease.")
        return token

    owner = acquire_owner("journal-diagnostic")
    assert owner is not None
    store.transition(run_id, RunState.PLANNING, owner=owner)
    store.transition(run_id, RunState.RUNNING, owner=owner)
    journal = CommandJournal(store)
    artifacts = store.path.parent / "artifacts"
    artifacts.mkdir()

    def register(identity: str, kind: str, data: bytes) -> str:
        name = f"{uuid4()}.{kind}.txt"
        with (artifacts / name).open("xb") as target:
            target.write(data)
        return store.register_artifact(
            run_id, owner, name, provenance=f"execution.{kind}:{identity}"
        )["id"]

    def invoke(
        payload: dict[str, Any], *, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            invocation,
            input=json.dumps(payload).encode("utf-8"),
            check=check,
            capture_output=True,
            timeout=45,
            creationflags=0x08000000,
        )

    def control(action: str, intent: CommandIntent) -> dict[str, Any]:
        response = json.loads(
            invoke({"action": action, "execution_id": intent.execution_id}).stdout
        )
        assert response["execution_id"] == intent.execution_id
        return response

    def prepare(call: str, argv: list[str], seconds: int = 10):
        intent = journal.reserve(
            run_id, owner, call, {"argv": argv, "max_seconds": seconds}, idempotency_key=call
        )
        snapshot = capture_workspace(project)
        artifact = register(intent.execution_id, "snapshot", snapshot.manifest_bytes())
        journal.snapshot_ready(run_id, owner, call, snapshot, artifact)
        reopened = RunStore(store.path).unresolved_tool_calls(run_id)
        assert any(item["arguments"]["execution_id"] == intent.execution_id for item in reopened)
        authorized = journal.authorize_launch(run_id, owner, call)
        assert authorized == intent
        payload = {
            "action": "run",
            "execution_id": intent.execution_id,
            "argv": intent.argv,
            "max_seconds": intent.max_seconds,
            "snapshot_b64": base64.b64encode(snapshot.to_bytes()).decode(),
        }
        return intent, snapshot, payload

    def seal_unknown(call: str, intent: CommandIntent) -> dict[str, Any]:
        acknowledgement = control("cleanup", intent)
        assert acknowledgement["cleanup_confirmed"] is True
        deadline = time.monotonic() + 5
        while True:
            status = control("status", intent)
            if not status["manager_alive"]:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Execution manager remains alive; do not release admission.")
            time.sleep(0.1)
        proof = CommandCleanupEvidence(
            execution_id=intent.execution_id,
            distribution=intent.distribution,
            profile_sha256=intent.profile_sha256,
            closed=status["closed"],
            unit_stopped=acknowledgement["cleanup_confirmed"],
            cleanup_confirmed=acknowledgement["cleanup_confirmed"],
            manager_alive=status["manager_alive"],
            manager_outcome_available=status["outcome"] is not None,
        )
        proof_id = register(intent.execution_id, "cleanup", proof.model_dump_json().encode())
        result = journal.close_interrupted(run_id, owner, call, proof_id)
        assert result["verification"] == "not_verified" and "exit_code" not in result
        return result

    first, snapshot, payload = prepare(
        "complete-handoff",
        [
            "/usr/bin/python3",
            "-I",
            "-c",
            "import pathlib;print(pathlib.Path('fixture.txt').read_text())",
        ],
    )
    response = invoke(payload)
    observed = json.loads(response.stdout)
    assert observed["execution_id"] == first.execution_id and observed["succeeded"]
    output_ids = {}
    for stream in ("stdout", "stderr"):
        data = base64.b64decode(observed[stream + "_b64"], validate=True)
        output_ids[stream + "_artifact_id"] = register(first.execution_id, stream, data)
    assert nonce.encode() in base64.b64decode(observed["stdout_b64"])
    store.release_lease(run_id, owner)
    old_owner = owner
    owner = acquire_owner("recovery-after-result")
    assert owner is not None
    receipt = control("status", first)
    assert receipt["closed"] and receipt["outcome"]["state"] == "finished"
    backend = receipt["outcome"]["result"]
    completion = CommandOutcome.model_validate(
        {
            **{key: backend[key] for key in CommandOutcome.model_fields if key in backend},
            **output_ids,
        }
    )
    try:
        journal.complete(run_id, old_owner, "complete-handoff", completion)
    except LeaseLost:
        pass
    else:
        raise AssertionError("Stale owner committed a command result")
    assert len(store.unresolved_tool_calls(run_id)) == 1
    completed = journal.complete(run_id, owner, "complete-handoff", completion)
    assert completed["succeeded"] and nonce in completed["stdout_excerpt"]
    assert not store.unresolved_tool_calls(run_id)

    late, _, late_payload = prepare(
        "delayed-launch", ["/usr/bin/python3", "-I", "-c", "print('MUST-NOT-RUN')"]
    )
    store.release_lease(run_id, owner)
    owner = acquire_owner("recovery-before-transport")
    assert owner is not None
    try:
        journal.authorize_launch(run_id, owner, "delayed-launch")
    except ReconciliationRequired:
        pass
    else:
        raise AssertionError("New lease authorized an old command")
    unverified = seal_unknown("delayed-launch", late)
    rejected = invoke(late_payload, check=False)
    (root / "delayed-launch.stderr.log").write_bytes(rejected.stderr)
    assert rejected.returncode != 0 and b"identity is closed" in rejected.stderr

    crash, _, crash_payload = prepare(
        "manager-death",
        [
            "/usr/bin/python3",
            "-I",
            "-c",
            "import subprocess,time;subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(30)']);time.sleep(30)",
        ],
        30,
    )
    child = subprocess.Popen(
        invocation,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=0x08000000,
    )
    assert child.stdin is not None
    child.stdin.write(json.dumps(crash_payload).encode())
    child.stdin.close()
    try:
        deadline = time.monotonic() + 10
        while True:
            status = control("status", crash)
            unit = "xaa-exec-" + crash.execution_id + ".service"
            query = subprocess.run(
                [
                    *prefix,
                    "/usr/bin/python3",
                    "-I",
                    "-c",
                    "import json,pathlib,subprocess,sys;g=subprocess.run(['/usr/bin/systemctl','show',sys.argv[1],'--property=ControlGroup','--value'],capture_output=True,text=True).stdout.strip();p=pathlib.Path('/sys/fs/cgroup')/g.lstrip('/')/'cgroup.procs';ids=p.read_text().split() if g and p.exists() else [];print(json.dumps([int(i) for i in ids if pathlib.Path('/proc',i,'comm').exists() and pathlib.Path('/proc',i,'comm').read_text().strip()=='python3']))",
                    unit,
                ],
                check=True,
                capture_output=True,
                timeout=3,
                creationflags=0x08000000,
            )
            workload = json.loads(query.stdout)
            if status["claim"] is not None and len(workload) >= 2:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Crash workload did not start")
            time.sleep(0.1)
        claim = status["claim"]
        subprocess.run(
            [
                *prefix,
                "/usr/bin/python3",
                "-I",
                "-c",
                "import os,pathlib,signal,sys;pid=int(sys.argv[1]);assert pathlib.Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]==sys.argv[2];assert pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==sys.argv[3];os.kill(pid,signal.SIGKILL)",
                str(claim["manager_pid"]),
                claim["manager_start"],
                claim["manager_boot_id"],
            ],
            check=True,
            capture_output=True,
            timeout=5,
            creationflags=0x08000000,
        )
        child.wait(timeout=10)
        assert child.returncode != 0
        assert len(RunStore(store.path).unresolved_tool_calls(run_id)) == 1
        store.release_lease(run_id, owner)
        owner = acquire_owner("recovery-after-manager-death")
        assert owner is not None
        recovered = seal_unknown("manager-death", crash)
        assert not recovered["manager_outcome_available"]
        rejected = invoke(crash_payload, check=False)
        assert rejected.returncode != 0
    finally:
        control("cleanup", crash)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    stopped, _, stopped_payload = prepare(
        "cancelled-run", ["/usr/bin/python3", "-I", "-c", "print('MUST-NOT-RUN')"]
    )
    cancelled = store.transition(run_id, RunState.CANCELLED, reason="user_requested_fixture")
    acknowledgement = control("cleanup", stopped)
    status = control("status", stopped)
    proof = CommandCleanupEvidence(
        execution_id=stopped.execution_id,
        distribution=stopped.distribution,
        profile_sha256=stopped.profile_sha256,
        closed=status["closed"],
        unit_stopped=acknowledgement["cleanup_confirmed"],
        cleanup_confirmed=acknowledgement["cleanup_confirmed"],
        manager_alive=status["manager_alive"],
        manager_outcome_available=status["outcome"] is not None,
    )
    stopped_result = journal.record_stopped_cleanup(
        run_id, cancelled["revision"], "cancelled-run", proof
    )
    stopped_run = store.get_run(run_id)
    assert stopped_run is not None
    assert stopped_result["verification"] == "not_verified" and stopped_run["state"] == "cancelled"
    assert invoke(stopped_payload, check=False).returncode != 0
    assert not store.unresolved_tool_calls(run_id)
    assert capture_workspace(project).sha256 == snapshot.sha256
    report = {
        "passed": True,
        "run_id": run_id,
        "profile_sha256": profile_digest,
        "source_hashes": fingerprints,
        "known_result_reconciled_without_relaunch": True,
        "stale_lease_rejected": True,
        "stopped_revision_cleanup_verified": True,
        "delayed_request_refused": True,
        "missing_result_not_fabricated": True,
        "observed_crash_workload_pids": workload,
        "unresolved_commands": 0,
        "original_project_unchanged": True,
        "artifact_count": len(store.list_artifacts(run_id)),
        "fault_actor": "automated_diagnostic_not_model",
        "ui_or_model_command_tool_enabled": False,
        "prelaunch_unverified_result": unverified,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Windows journal/WSL command acceptance passed: {root}")


if __name__ == "__main__":
    main()

"""Run imported-procedure Python/npm dependencies through a bound specialist profile."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from wsl_procedure_environment_acceptance import build_wheel, make_lock
from xaa.extensions.npm_dependencies import (
    NpmLockedPackage,
    NpmRootDependency,
    ProcedureNpmDependencyLock,
)
from xaa.runtime.command_controller import CommandController
from xaa.runtime.states import RunState
from xaa.runtime.task_commands import command_evidence
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_worker import execute_filesystem
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.runtime.wsl_transport import WSLTransport, profile_fingerprint
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.skills import SkillAsset, SkillDraft, SkillStore
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.tasks import TaskStore


def _wsl(distribution: str, *arguments: str) -> str:
    executable = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe")
    completed = subprocess.run(
        [executable, "--distribution", distribution, "--exec", *arguments],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr[-1200:] or "WSL fixture command failed.")
    return completed.stdout.strip()


def _asset(path: str, content: bytes, media_type: str) -> SkillAsset:
    return SkillAsset(
        path=path,
        content_base64=base64.b64encode(content).decode("ascii"),
        media_type=media_type,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _npm_tarball() -> bytes:
    members = {
        "package/package.json": json.dumps(
            {"name": "xaa-offline-fixture", "version": "1.0.0", "main": "index.js"},
            separators=(",", ":"),
        ).encode("utf-8"),
        "package/index.js": b"module.exports = 'profile-node-ready';\n",
    }
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path, content in members.items():
            entry = tarfile.TarInfo(path)
            entry.size = len(content)
            entry.mode = 0o644
            archive.addfile(entry, BytesIO(content))
    return output.getvalue()


def _npm_lock(
    *,
    skill_id: str,
    source_sha256: str,
    distribution: str,
    manifest: bytes,
    lockfile: bytes,
    archive: bytes,
) -> tuple[ProcedureNpmDependencyLock, bytes]:
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii")
    resolved_url = "https://registry.npmjs.org/xaa-offline-fixture/-/xaa-offline-fixture-1.0.0.tgz"
    parsed_manifest = json.loads(manifest)
    node_version = _wsl(distribution, "node", "--version")
    npm_version = _wsl(distribution, "npm", "--version")
    lock = ProcedureNpmDependencyLock(
        schema_version=1,
        skill_id=skill_id,
        skill_version=1,
        source_sha256=source_sha256,
        package_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        lockfile_sha256=hashlib.sha256(lockfile).hexdigest(),
        distribution=distribution,
        node_version=node_version,
        npm_version=npm_version,
        requirements=("xaa-offline-fixture@^1.0.0",),
        root_dependencies=(
            NpmRootDependency(
                name="xaa-offline-fixture", version_range="^1.0.0", kind="dependencies"
            ),
        ),
        packages=(
            NpmLockedPackage(
                package_path="node_modules/xaa-offline-fixture",
                name="xaa-offline-fixture",
                version="1.0.0",
                resolved_url=resolved_url,
                integrity=integrity,
            ),
        ),
    )
    if parsed_manifest.get("dependencies") != {"xaa-offline-fixture": "^1.0.0"}:
        raise ValueError("The generated npm manifest is not the expected fixture.")
    return lock, archive


async def diagnose(distribution: str, report_root: Path) -> Path:
    root = report_root.resolve() / str(uuid4())
    project = root / "project"
    project.mkdir(parents=True)
    marker = uuid4().hex
    (project / "fixture.txt").write_text(marker, encoding="utf-8")
    original_snapshot = capture_workspace(project)
    store = RunStore(root / "app-data" / "XAA" / "xaa.sqlite3")
    skill_store = SkillStore(store)

    python_leaf_name, python_leaf = build_wheel("xaa-leaf", "leaf-ready")
    python_top_name, python_top = build_wheel("xaa-top", "top-ready", "xaa-leaf==1.0")
    python_artifacts = {
        python_leaf_name: python_leaf,
        python_top_name: python_top,
    }
    python_script = (
        b"import pathlib,xaa_top,xaa_leaf; "
        b"print(xaa_top.VALUE, xaa_leaf.VALUE, pathlib.Path('fixture.txt').read_text())\n"
    )
    node_script = (
        b"const fs=require('fs'),value=require('xaa-offline-fixture'); "
        b"console.log(value,fs.readFileSync('fixture.txt','utf8'));\n"
    )
    package_manifest = json.dumps(
        {
            "name": "xaa-specialist-fixture",
            "version": "1.0.0",
            "dependencies": {"xaa-offline-fixture": "^1.0.0"},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    npm_archive = _npm_tarball()
    npm_integrity = "sha512-" + base64.b64encode(hashlib.sha512(npm_archive).digest()).decode(
        "ascii"
    )
    package_lock = json.dumps(
        {
            "name": "xaa-specialist-fixture",
            "version": "1.0.0",
            "lockfileVersion": 3,
            "requires": True,
            "packages": {
                "": {
                    "name": "xaa-specialist-fixture",
                    "version": "1.0.0",
                    "dependencies": {"xaa-offline-fixture": "^1.0.0"},
                },
                "node_modules/xaa-offline-fixture": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/xaa-offline-fixture/-/xaa-offline-fixture-1.0.0.tgz",
                    "integrity": npm_integrity,
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    source_content = "Imported fixture with exact Python and npm requirements."
    source_sha256 = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
    declarations = {
        "python_dependency_specs": ["xaa-top==1.0"],
        "npm_dependency_specs": ["xaa-offline-fixture@^1.0.0"],
        "unparsed_dependency_count": 0,
        "dependency_names_truncated": False,
    }
    imported = skill_store.import_agent_plugin(
        SkillDraft(
            name="Specialist dependency environment diagnostic",
            content=source_content,
            required_tools=[],
            required_permissions=["workspace.read"],
            assets=[
                _asset("scripts/check.py", python_script, "text/x-python"),
                _asset("scripts/check.cjs", node_script, "text/javascript"),
                _asset("package.json", package_manifest, "application/json"),
                _asset("package-lock.json", package_lock, "application/json"),
            ],
        ),
        {
            "content_sha256": source_sha256,
            "declared_requirements": json.dumps(declarations, separators=(",", ":")),
        },
        source_kind="agent_plugins_v1",
    )
    skill_id = str(imported["id"])
    python_lock = make_lock(
        distribution,
        python_artifacts,
        source_sha256=source_sha256,
    ).model_copy(update={"skill_id": skill_id})
    skill_store.wheelhouses.store(python_lock, python_artifacts)
    skill_store.save_python_dependency_lock(python_lock)
    skill_store.review_python_dependency_lock(
        skill_id, 1, python_lock.environment_digest(), "approved"
    )
    npm_lock, npm_archive = _npm_lock(
        skill_id=skill_id,
        source_sha256=source_sha256,
        distribution=distribution,
        manifest=package_manifest,
        lockfile=package_lock,
        archive=npm_archive,
    )
    skill_store.save_npm_dependency_lock(npm_lock)
    with tempfile.TemporaryDirectory(prefix="xaa-specialist-npm-fixture-") as temp_dir:
        tarball = Path(temp_dir) / "fixture.tgz"
        tarball.write_bytes(npm_archive)
        skill_store.store_npm_dependency_tarballs(
            npm_lock, {npm_lock.packages[0].package_path: tarball}
        )
        skill_store.review_npm_dependency_lock(
            skill_id, 1, npm_lock.environment_digest(), "approved"
        )
        with store.connection() as connection:
            connection.execute(
                "UPDATE skills SET lifecycle_state='active',active_version=1 WHERE id=?",
                (skill_id,),
            )
        transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
        python_install = await transport.install_procedure_environment(
            python_lock,
            skill_store.wheelhouses.root / python_lock.environment_digest(),
            deadline=time.monotonic() + 180,
        )
        skill_store.record_python_dependency_environment(python_lock, python_install)
        npm_install = await transport.install_node_environment(
            npm_lock,
            package_manifest,
            package_lock,
            skill_store.npm_tarballs.root / npm_lock.environment_digest(),
            deadline=time.monotonic() + 180,
        )
        skill_store.record_npm_dependency_environment(npm_lock, npm_install)

        python_digest = python_lock.environment_digest()
        npm_digest = npm_lock.environment_digest()
        profile_store = SpecialistProfileStore(store)
        profile_store.set_procedure(
            "executor",
            skill_id,
            python_environment_digest=python_digest,
            npm_environment_digest=npm_digest,
        )
        profile_sha256 = profile_fingerprint()[0]
        run = store.create_run(
            objective="Execute imported scripts using the dependency environments bound to the executor profile.",
            acceptance_criteria=[
                "The Python script imports both exact locked wheels.",
                "The Node.js script imports the exact offline npm tarball.",
                "Both scripts read the private project fixture.",
            ],
            workspace=str(project),
            mode="workspace",
            model=None,
            command_grant={"distribution": distribution, "profile_sha256": profile_sha256},
        )
        run_id = str(run["id"])
        owner = store.acquire_lease(
            run_id, "specialist-dependency-environment-diagnostic", seconds=300
        )
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
                    id="dependency-check",
                    objective="Run the imported Python and Node dependency check scripts.",
                    acceptance_criteria=("Both locked dependencies and the fixture are printed.",),
                    permissions=("workspace.read", "workspace.write", "workspace.command"),
                )
            ],
        )
        claim = tasks.claim_ready(run_id, owner)
        if claim is None:
            raise RuntimeError("Could not claim the specialist dependency task.")
        scope = TaskReadScope(run_id, owner, "dependency-check", claim["token"])
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
        skill_root = f"__xaa_selected_skill__/{skill_id}/v1/scripts"
        controller = CommandController(store, transport, task_scope=scope)
        commands: list[dict[str, Any]] = []
        for call_id, argv, expected, env_manager in (
            (
                "specialist-python-dependency",
                ["python", "-I", f"{skill_root}/check.py"],
                ("top-ready", "leaf-ready", marker),
                "python",
            ),
            (
                "specialist-node-dependency",
                ["node", "--no-addons", f"{skill_root}/check.cjs"],
                ("profile-node-ready", marker),
                "npm",
            ),
        ):
            result = await controller.execute(
                run_id,
                owner,
                call_id,
                {"argv": argv},
                idempotency_key=call_id,
                deadline=time.monotonic() + 40,
            )
            output = str(result.get("stdout_excerpt", ""))
            if not result.get("succeeded") or any(value not in output for value in expected):
                raise AssertionError(
                    f"{env_manager} dependency script did not return its exact outputs."
                )
            with store.connection() as connection:
                saved_call = connection.execute(
                    "SELECT arguments FROM tool_calls WHERE run_id=? AND provider_call_id=? "
                    "AND state='completed'",
                    (run_id, call_id),
                ).fetchone()
            if saved_call is None:
                raise AssertionError("Completed specialist command arguments were not persisted.")
            saved_arguments = json.loads(saved_call["arguments"])
            argument_key = (
                "procedure_environment_digest"
                if env_manager == "python"
                else "procedure_node_environment_digest"
            )
            actual_digest = saved_arguments.get(argument_key)
            expected_digest = python_digest if env_manager == "python" else npm_digest
            if actual_digest != expected_digest:
                raise AssertionError(
                    f"{env_manager} profile environment was not bound to its command."
                )
            commands.append(
                {
                    "manager": env_manager,
                    "environment_digest": actual_digest,
                    "stdout_excerpt": output,
                    "succeeded": True,
                }
            )
        evidence = command_evidence(store, run_id, scope.task_id)
        if len(evidence) != 2 or not all(item["matches_current_private_tree"] for item in evidence):
            raise AssertionError(
                "Specialist command evidence does not match its augmented snapshot."
            )
        if capture_workspace(project).sha256 != original_snapshot.sha256:
            raise AssertionError("Specialist dependency execution changed the original project.")
        if store.unresolved_tool_calls(run_id):
            raise AssertionError("A specialist dependency command remains unresolved.")
        store.transition(run_id, RunState.CANCELLED, reason="diagnostic_complete")
        report = {
            "passed": True,
            "distribution": distribution,
            "skill_id": skill_id,
            "profile_sha256": profile_sha256,
            "required_dependency_managers": ["python", "npm"],
            "python_environment_digest": python_digest,
            "python_environment_tree_sha256": python_install["tree_sha256"],
            "npm_environment_digest": npm_digest,
            "npm_environment_tree_sha256": npm_install["tree_sha256"],
            "task_id": scope.task_id,
            "commands": commands,
            "command_evidence_matches_current_private_tree": True,
            "original_project_unchanged": True,
            "unresolved_commands": 0,
            "run_state": "cancelled_after_diagnostic",
            "model_inference": False,
            "registry_access": False,
        }
        report_path = root / "report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Specialist dependency environment execution passed: {report_path}")
        await transport.remove_procedure_environment(python_digest, deadline=time.monotonic() + 30)
        await transport.remove_node_environment(npm_digest, deadline=time.monotonic() + 30)
        skill_store.forget_python_dependency_environment(skill_id, 1, python_digest)
        skill_store.forget_npm_dependency_environment(skill_id, 1, npm_digest)
    return report_path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument(
        "--report-root", type=Path, default=Path(".acceptance/specialist-dependency-environment")
    )
    args = parser.parse_args()
    asyncio.run(diagnose(args.distribution, args.report_root))


if __name__ == "__main__":
    main()

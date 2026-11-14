"""Acquire a pinned npm graph and run it in its bound offline WSL environment."""

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

from xaa.extensions.npm_dependencies import (
    NpmLockedPackage,
    NpmRootDependency,
    ProcedureNpmDependencyLock,
)
from xaa.extensions.npm_tarball_storage import ProcedureNpmTarballStore
from xaa.extensions.npm_tarballs import acquire_npm_tarballs_in_appcontainer
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.owned_transport import TransportError
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport

ROOT_PACKAGE = (
    "is-odd",
    "3.0.1",
    "https://registry.npmjs.org/is-odd/-/is-odd-3.0.1.tgz",
    "sha512-CQpnWPrDwmP1+SMHXZhtLtJv90yiyVfluGsX5iNCVkrhQtU3TQHsUWPG9wkdk9Lgd5yNpAg9jQEo90CBaXgWMA==",
)
TRANSITIVE_PACKAGE = (
    "is-number",
    "6.0.0",
    "https://registry.npmjs.org/is-number/-/is-number-6.0.0.tgz",
    "sha512-Wu1VHeILBK8KAWJUAiSZQX94GmOE45Rg6/538fKwiloUu21KncEkYGPqob2oSZ5mUT73vLGrHQjKw3KMPwfDzg==",
)


async def run(distribution: str, report_path: Path) -> dict[str, Any]:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
    node_version = await asyncio.to_thread(_wsl, distribution, "node", "--version")
    npm_version = await asyncio.to_thread(_wsl, distribution, "npm", "--version")
    root_name, root_version, root_url, root_integrity = ROOT_PACKAGE
    transitive_name, transitive_version, transitive_url, transitive_integrity = TRANSITIVE_PACKAGE
    manifest = json.dumps(
        {
            "name": "xaa-live-registry-acceptance",
            "version": "1.0.0",
            "dependencies": {root_name: f"^{root_version}"},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    package_lock = json.dumps(
        {
            "name": "xaa-live-registry-acceptance",
            "version": "1.0.0",
            "lockfileVersion": 3,
            "requires": True,
            "packages": {
                "": {
                    "name": "xaa-live-registry-acceptance",
                    "version": "1.0.0",
                    "dependencies": {root_name: f"^{root_version}"},
                },
                f"node_modules/{root_name}": {
                    "version": root_version,
                    "resolved": root_url,
                    "integrity": root_integrity,
                    "dependencies": {transitive_name: "^6.0.0"},
                },
                f"node_modules/{transitive_name}": {
                    "version": transitive_version,
                    "resolved": transitive_url,
                    "integrity": transitive_integrity,
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    lock = ProcedureNpmDependencyLock(
        schema_version=1,
        skill_id=str(uuid4()),
        skill_version=1,
        source_sha256=hashlib.sha256(manifest + b"\0" + package_lock).hexdigest(),
        package_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        lockfile_sha256=hashlib.sha256(package_lock).hexdigest(),
        distribution=distribution,
        node_version=node_version,
        npm_version=npm_version,
        requirements=(f"{root_name}@^{root_version}",),
        root_dependencies=(
            NpmRootDependency(
                name=root_name, version_range=f"^{root_version}", kind="dependencies"
            ),
        ),
        packages=(
            NpmLockedPackage(
                package_path=f"node_modules/{root_name}",
                name=root_name,
                version=root_version,
                resolved_url=root_url,
                integrity=root_integrity,
            ),
            NpmLockedPackage(
                package_path=f"node_modules/{transitive_name}",
                name=transitive_name,
                version=transitive_version,
                resolved_url=transitive_url,
                integrity=transitive_integrity,
            ),
        ),
    )
    store = ProcedureNpmTarballStore(report_path.parent / "tarballs")
    acquired: dict[str, Any] = {}

    async def persist(files: dict[str, Path], verified: dict[str, Any]) -> None:
        store.store_files(lock, files)
        acquired.update(verified)

    await acquire_npm_tarballs_in_appcontainer(lock, on_verified_files=persist)
    if set(acquired) != {package.package_path for package in lock.packages}:
        raise RuntimeError(
            "The isolated npm acquisition worker returned an incomplete package set."
        )

    digest = lock.environment_digest()
    installed: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    try:
        installed = await transport.install_node_environment(
            lock,
            manifest,
            package_lock,
            store.root / digest,
            deadline=time.monotonic() + 300,
        )
        script_path = f"__xaa_selected_skill__/{lock.skill_id}/v1/check.cjs"
        procedure_path = f"__xaa_selected_skill__/{lock.skill_id}/v1"
        script = (
            "const fs=require('fs'),isOdd=require('is-odd');"
            "if(isOdd(7)!==true||isOdd(2)!==false||isOdd(-3)!==true)process.exit(10);"
            f"if(fs.realpathSync('{procedure_path}/node_modules')!=="
            "'/opt/xaa/procedure-node/node_modules')process.exit(11);"
            "if(fs.existsSync('node_modules'))process.exit(12);"
            "process.stdout.write('transitive-package-check-passed');"
        ).encode("ascii")
        snapshot = WorkspaceSnapshot(
            files=(SnapshotFile(path=script_path, content=script),),
            directories=(
                "__xaa_selected_skill__",
                f"__xaa_selected_skill__/{lock.skill_id}",
                procedure_path,
            ),
        )
        intent = CommandIntent(
            argv=["node", script_path],
            max_seconds=15,
            execution_id=uuid4().hex,
            backend="wsl-bubblewrap-v1",
            distribution=distribution,
            profile_sha256=transport.profile_sha256,
            workspace="/workspace/npm-live-registry-acceptance",
            procedure_node_environment_digest=digest,
            procedure_node_environment_tree_sha256=installed["tree_sha256"],
            procedure_skill_id=lock.skill_id,
            procedure_version=lock.skill_version,
        )
        execution = await transport.run(intent, snapshot, deadline=time.monotonic() + 30)
        stdout = base64.b64decode(execution.get("stdout_b64", ""), validate=True).decode("utf-8")
        if (
            execution.get("succeeded") is not True
            or execution.get("cleanup_confirmed") is not True
            or execution.get("procedure_node_environment_digest") != digest
            or stdout != "transitive-package-check-passed"
        ):
            raise RuntimeError("The real npm graph failed its selected-procedure WSL run.")
        report = {
            "acquisition_worker": "Windows AppContainer",
            "packages": [
                {
                    "archive_size_bytes": acquired[package.package_path].archive_size_bytes,
                    "integrity": package.integrity,
                    "name": package.name,
                    "resolved_url": package.resolved_url,
                    "version": package.version,
                }
                for package in lock.packages
            ],
            "distribution": distribution,
            "environment_digest": digest,
            "installed_tree_sha256": installed["tree_sha256"],
            "module_check": stdout,
            "network_during_install_and_run": "none",
            "npm_runtime": npm_version,
            "system_node": node_version,
            "workspace_node_modules_absent": True,
            "procedure_environment_scoped": True,
            "execution_cleanup_confirmed": True,
        }
    finally:
        try:
            await transport.remove_node_environment(digest, deadline=time.monotonic() + 30)
        except TransportError:
            status = await transport.node_environment_status(digest, deadline=time.monotonic() + 30)
            if status.get("installed") is not False:
                raise
    status = await transport.node_environment_status(digest, deadline=time.monotonic() + 30)
    if status.get("installed") is not False:
        raise RuntimeError("The exact npm environment was not removed after acceptance.")
    report["exact_environment_removed"] = True
    return report


def _wsl(distribution: str, *argv: str) -> str:
    import subprocess

    process = subprocess.run(
        ["wsl.exe", "--distribution", distribution, "--user", "root", "--exec", *argv],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(process.stderr[-1200:] or "WSL runtime query failed.")
    return process.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    report = asyncio.run(run(arguments.distribution, arguments.report))
    arguments.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

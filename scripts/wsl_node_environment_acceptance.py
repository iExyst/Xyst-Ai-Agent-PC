"""Exercise offline npm installation, lifecycle suppression, and exact cleanup in WSL."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from xaa.extensions.npm_dependencies import (
    NpmLockedPackage,
    NpmRootDependency,
    ProcedureNpmDependencyLock,
)
from xaa.extensions.npm_tarball_storage import ProcedureNpmTarballStore
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.owned_transport import EnvironmentInUseError, TransportError
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport


def _tarball(*, extra_file_count: int = 0) -> bytes:
    package = {
        "name": "xaa-offline-fixture",
        "version": "1.0.0",
        "main": "index.js",
        "bin": {"xaa-offline-cli": "cli.js"},
        "scripts": {"install": "node install.js"},
    }
    members = {
        "package/package.json": json.dumps(package, separators=(",", ":")).encode(),
        "package/index.js": b"module.exports = 'offline-ready';\n",
        "package/cli.js": b"#!/usr/bin/env node\nprocess.stdout.write('offline-cli');\n",
        "package/install.js": b"require('fs').writeFileSync(__dirname + '/install-ran.txt', 'bad');\n",
    }
    members.update(
        {
            f"package/recovery-fixture/{index:05d}.txt": b"recovery-fixture\n"
            for index in range(extra_file_count)
        }
    )
    output = io.BytesIO()
    import tarfile

    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith("cli.js") else 0o644
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _wsl_binary() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe")


def _wsl(distribution: str, *arguments: str) -> str:
    completed = subprocess.run(
        [_wsl_binary(), "--distribution", distribution, "--exec", *arguments],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr[-1200:] or "WSL fixture command failed.")
    return completed.stdout.strip()


def _seed_interrupted_install(distribution: str, digest: str) -> None:
    target = PurePosixPath("/var/lib/xaa-node-environments") / digest
    unit = f"xaa-nodeenv-{digest[:32]}.service"
    _wsl(
        distribution,
        "sudo",
        "install",
        "-d",
        "--",
        str(target / "environment/project"),
    )
    _wsl(distribution, "sudo", "touch", "--", str(target / "environment/project/.partial"))
    _wsl(
        distribution,
        "sudo",
        "systemd-run",
        "--quiet",
        f"--unit={unit}",
        "/bin/sleep",
        "300",
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        active = _wsl(distribution, "sudo", "systemctl", "is-active", unit)
        if active == "active":
            return
        time.sleep(0.1)
    raise RuntimeError("Could not seed the active interrupted-install fixture unit.")


async def _kill_and_retry_install(
    distribution: str,
    transport: WSLTransport,
    lock: ProcedureNpmDependencyLock,
    package_manifest: bytes,
    package_lock: bytes,
    directory: Path,
) -> dict[str, Any]:
    digest = lock.environment_digest()
    unit = f"xaa-nodeenv-{digest[:32]}.service"
    install_task = asyncio.create_task(
        transport.install_node_environment(
            lock,
            package_manifest,
            package_lock,
            directory,
            deadline=time.monotonic() + 300,
        )
    )
    deadline = time.monotonic() + 90
    killed = False
    while time.monotonic() < deadline and not install_task.done():
        state = await asyncio.to_thread(
            _wsl,
            distribution,
            "sudo",
            "systemctl",
            "show",
            unit,
            "--property=ActiveState",
            "--value",
        )
        if state == "active":
            await asyncio.sleep(0.2)
            if not install_task.done():
                _wsl(
                    distribution,
                    "sudo",
                    "systemctl",
                    "kill",
                    "--kill-whom=all",
                    "--signal=SIGKILL",
                    unit,
                )
                killed = True
                break
        await asyncio.sleep(0.05)
    if not killed:
        if not install_task.done():
            await install_task
        raise RuntimeError("Could not interrupt the live npm installation service.")
    try:
        await install_task
    except TransportError:
        pass
    else:
        raise RuntimeError("The npm installer completed despite the requested process kill.")
    status = await transport.node_environment_status(digest, deadline=time.monotonic() + 30)
    if status.get("installed") is not False:
        raise RuntimeError("Killed npm install published an environment instead of cleaning up.")
    recovered = await transport.install_node_environment(
        lock,
        package_manifest,
        package_lock,
        directory,
        deadline=time.monotonic() + 300,
    )
    if recovered.get("installed") is not True:
        raise RuntimeError("Retry after killing npm install did not recover the environment.")
    return recovered


async def run(distribution: str, report_path: Path) -> dict[str, Any]:
    node_version = _wsl(distribution, "node", "--version")
    npm_version = _wsl(distribution, "npm", "--version")
    archive = _tarball(extra_file_count=4096)
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii")
    resolved = "https://registry.npmjs.org/xaa-offline-fixture/-/xaa-offline-fixture-1.0.0.tgz"
    package_manifest = json.dumps(
        {
            "name": "xaa-probe-project",
            "version": "1.0.0",
            "dependencies": {"xaa-offline-fixture": "^1.0.0"},
        },
        separators=(",", ":"),
    ).encode()
    lock_document = {
        "name": "xaa-probe-project",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {
                "name": "xaa-probe-project",
                "version": "1.0.0",
                "dependencies": {"xaa-offline-fixture": "^1.0.0"},
            },
            "node_modules/xaa-offline-fixture": {
                "version": "1.0.0",
                "resolved": resolved,
                "integrity": integrity,
                "hasInstallScript": True,
                "bin": {"xaa-offline-cli": "cli.js"},
            },
        },
    }
    package_lock = json.dumps(lock_document, separators=(",", ":")).encode()
    lock = ProcedureNpmDependencyLock(
        schema_version=1,
        skill_id=str(uuid4()),
        skill_version=1,
        source_sha256="a" * 64,
        package_manifest_sha256=hashlib.sha256(package_manifest).hexdigest(),
        lockfile_sha256=hashlib.sha256(package_lock).hexdigest(),
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
                resolved_url=resolved,
                integrity=integrity,
            ),
        ),
    )
    archive_path = report_path.parent / "fixture.tgz"
    archive_path.write_bytes(archive)
    store = ProcedureNpmTarballStore(report_path.parent / "tarballs")
    store.store_files(lock, {lock.packages[0].package_path: archive_path})
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
    directory = store.root / lock.environment_digest()
    installed: dict[str, Any] | None = None
    try:
        _seed_interrupted_install(distribution, lock.environment_digest())
        installed = await transport.install_node_environment(
            lock, package_manifest, package_lock, directory, deadline=time.monotonic() + 300
        )
        await transport.remove_node_environment(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        installed = await _kill_and_retry_install(
            distribution, transport, lock, package_manifest, package_lock, directory
        )
        repeated = await transport.install_node_environment(
            lock, package_manifest, package_lock, directory, deadline=time.monotonic() + 60
        )
        observed = await transport.node_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        remote_root = (
            PurePosixPath("/var/lib/xaa-node-environments")
            / lock.environment_digest()
            / "environment/project"
        )
        module_value = _wsl(
            distribution,
            "node",
            "-e",
            "const fs=require('fs'),m=require(process.argv[1]);"
            "if(m!=='offline-ready')process.exit(4);"
            "if(fs.existsSync(process.argv[2]))process.exit(5);"
            "if(!fs.lstatSync(process.argv[3]).isSymbolicLink())process.exit(6);"
            "if(fs.existsSync(process.argv[4]))process.exit(7);"
            "process.stdout.write(m)",
            str(remote_root / "node_modules/xaa-offline-fixture"),
            str(remote_root / "node_modules/xaa-offline-fixture/install-ran.txt"),
            str(remote_root / "node_modules/.bin/xaa-offline-cli"),
            str(remote_root / ".partial"),
        )
        recovery_unit_state = _wsl(
            distribution,
            "sudo",
            "systemctl",
            "show",
            f"xaa-nodeenv-{lock.environment_digest()[:32]}.service",
            "--property=ActiveState",
            "--value",
        )
        if (
            installed.get("installed") is not True
            or repeated.get("tree_sha256") != installed.get("tree_sha256")
            or observed.get("tree_sha256") != installed.get("tree_sha256")
            or module_value != "offline-ready"
            or recovery_unit_state != "inactive"
        ):
            raise RuntimeError(
                "Offline npm installation, reuse, module loading, or lifecycle suppression failed."
            )
        script_path = f"__xaa_selected_skill__/{lock.skill_id}/v1/check.cjs"
        script = (
            "const value=require('xaa-offline-fixture');"
            "setTimeout(()=>process.stdout.write(value),7000);"
        ).encode("ascii")
        snapshot = WorkspaceSnapshot(
            files=(SnapshotFile(script_path, script),),
            directories=(
                "__xaa_selected_skill__",
                f"__xaa_selected_skill__/{lock.skill_id}",
                f"__xaa_selected_skill__/{lock.skill_id}/v1",
            ),
        )
        intent = CommandIntent(
            argv=["node", script_path],
            max_seconds=15,
            execution_id=uuid.uuid4().hex,
            backend="wsl-bubblewrap-v1",
            distribution=distribution,
            profile_sha256=transport.profile_sha256,
            workspace="/workspace/acceptance",
            procedure_node_environment_digest=lock.environment_digest(),
            procedure_node_environment_tree_sha256=installed["tree_sha256"],
            procedure_skill_id=lock.skill_id,
            procedure_version=1,
        )
        execution = asyncio.create_task(
            transport.run(intent, snapshot, deadline=time.monotonic() + 30)
        )
        await asyncio.sleep(2)
        try:
            await transport.remove_node_environment(
                lock.environment_digest(), deadline=time.monotonic() + 20
            )
        except EnvironmentInUseError:
            removal_refused_while_active = True
        else:
            removal_refused_while_active = False
        executed = await execution
        if (
            not removal_refused_while_active
            or executed.get("succeeded") is not True
            or executed.get("stdout_b64") != base64.b64encode(b"offline-ready").decode("ascii")
            or executed.get("cleanup_confirmed") is not True
            or executed.get("procedure_node_environment_digest") != lock.environment_digest()
        ):
            raise RuntimeError(
                "Procedure-scoped Node module loading or active-use lease verification failed: "
                + json.dumps(
                    {
                        "removal_refused_while_active": removal_refused_while_active,
                        "succeeded": executed.get("succeeded"),
                        "termination_reason": executed.get("termination_reason"),
                        "stdout": executed.get("stdout_b64"),
                        "stderr": executed.get("stderr_b64"),
                        "cleanup_confirmed": executed.get("cleanup_confirmed"),
                        "environment_digest": executed.get("procedure_node_environment_digest"),
                    },
                    sort_keys=True,
                )
            )
        removed = await transport.remove_node_environment(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        after = await transport.node_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if removed.get("removed") is not True or after.get("installed") is not False:
            raise RuntimeError("Exact npm environment cleanup failed.")
        return {
            "distribution": distribution,
            "environment_digest": lock.environment_digest(),
            "tree_sha256": installed["tree_sha256"],
            "installed_bytes": installed["installed_bytes"],
            "file_count": installed["file_count"],
            "offline_locked_module_loaded": module_value,
            "procedure_run_module_loaded": True,
            "removal_refused_while_active": removal_refused_while_active,
            "lifecycle_script_marker_absent": True,
            "idempotent_install": True,
            "interrupted_partial_tree_recovered": True,
            "installer_process_killed_and_recovered": True,
            "recovery_unit_inactive": True,
            "integrity_status": True,
            "exact_environment_removed": True,
            "network": "none",
            "cleanup_confirmed": True,
        }
    finally:
        await transport.remove_node_environment(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )


async def remove_environment(distribution: str, digest: str) -> dict[str, Any]:
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
    return await transport.remove_node_environment(digest, deadline=time.monotonic() + 30)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--remove-digest")
    args = parser.parse_args()
    if args.remove_digest:
        result = asyncio.run(remove_environment(args.distribution, args.remove_digest))
        print(json.dumps(result, sort_keys=True))
        return
    report_path = (
        args.report
        or Path(".acceptance") / f"npm-node-environment-{uuid.uuid4().hex}" / "report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(run(args.distribution, report_path))
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

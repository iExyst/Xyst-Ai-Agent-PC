"""Exercise offline npm installation, lifecycle suppression, and exact cleanup in WSL."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, cast
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


def _tarball(
    *, extra_file_count: int = 0, version: str = "1.0.0", module_value: str = "offline-ready"
) -> bytes:
    package = {
        "name": "xaa-offline-fixture",
        "version": version,
        "main": "index.js",
        "bin": {"xaa-offline-cli": "cli.js"},
        "scripts": {"install": "node install.js"},
    }
    members = {
        "package/package.json": json.dumps(package, separators=(",", ":")).encode(),
        "package/index.js": f"module.exports = {json.dumps(module_value)};\n".encode(),
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
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith("cli.js") else 0o644
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _package_tarball(package: dict[str, object], files: dict[str, bytes]) -> bytes:
    members = {"package/package.json": json.dumps(package, separators=(",", ":")).encode()}
    members.update({f"package/{name}": data for name, data in files.items()})
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _procedure_probe(
    lock: ProcedureNpmDependencyLock, expected_value: str
) -> tuple[str, WorkspaceSnapshot]:
    procedure_path = f"__xaa_selected_skill__/{lock.skill_id}/v{lock.skill_version}"
    script_path = f"{procedure_path}/check.cjs"
    script = (
        "const fs=require('fs'),value=require('xaa-offline-fixture');"
        f"if(value!=={json.dumps(expected_value)})process.exit(10);"
        f"if(fs.realpathSync('{procedure_path}/node_modules')!=='/opt/xaa/procedure-node/node_modules')process.exit(8);"
        "if(fs.existsSync('node_modules'))process.exit(9);"
        "setTimeout(()=>process.stdout.write(value),7000);"
    ).encode("ascii")
    snapshot = WorkspaceSnapshot(
        files=(SnapshotFile(script_path, script),),
        directories=(
            "__xaa_selected_skill__",
            f"__xaa_selected_skill__/{lock.skill_id}",
            procedure_path,
        ),
    )
    return script_path, snapshot


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


def _system_runtime_snapshot(distribution: str) -> dict[str, Any]:
    node_path = _wsl(distribution, "readlink", "-f", "/usr/bin/node")
    npm_path = _wsl(distribution, "readlink", "-f", "/usr/bin/npm")
    global_root = _wsl(distribution, "npm", "root", "-g")
    npm_tree = str(PurePosixPath(npm_path).parent.parent)
    digest_script = (
        "import hashlib,json,os,stat,sys\n"
        "def digest(raw):\n"
        " root=__import__('pathlib').Path(raw)\n"
        " if not root.exists(): return None\n"
        " h=hashlib.sha256()\n"
        " paths=[root,*sorted(root.rglob('*'),key=lambda p:p.relative_to(root).as_posix())] if root.is_dir() else [root]\n"
        " for p in paths:\n"
        "  i=p.lstat(); rel='.' if p==root else p.relative_to(root).as_posix()\n"
        "  h.update(rel.encode()+b'\\0'+stat.S_IMODE(i.st_mode).to_bytes(2,'big'))\n"
        "  if stat.S_ISLNK(i.st_mode): h.update(b'l'+os.readlink(p).encode())\n"
        "  elif stat.S_ISREG(i.st_mode):\n"
        "   h.update(b'f')\n"
        "   with p.open('rb') as f:\n"
        "    while c:=f.read(1048576): h.update(c)\n"
        "  elif stat.S_ISDIR(i.st_mode): h.update(b'd')\n"
        "  else: raise ValueError('system runtime contains a special file')\n"
        " return h.hexdigest()\n"
        "print(json.dumps({'node':digest(sys.argv[1]),'npm':digest(sys.argv[2]),'global':digest(sys.argv[3])},sort_keys=True))\n"
    )
    try:
        hashes = json.loads(
            _wsl(distribution, "python3", "-c", digest_script, node_path, npm_tree, global_root)
        )
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Could not capture the WSL system runtime inventory.") from exc
    if not isinstance(hashes, dict):
        raise TypeError("WSL system runtime inventory is malformed.")
    typed_hashes = cast(dict[str, object], hashes)
    runtime_hashes = {name: typed_hashes.get(name) for name in ("node", "npm", "global")}
    if any(value is not None and not isinstance(value, str) for value in runtime_hashes.values()):
        raise TypeError("WSL system runtime inventory contains invalid hashes.")
    return {
        "node_version": _wsl(distribution, "node", "--version"),
        "npm_version": _wsl(distribution, "npm", "--version"),
        "node_path": node_path,
        "node_tree_sha256": runtime_hashes["node"],
        "npm_path": npm_path,
        "npm_tree_sha256": runtime_hashes["npm"],
        "global_root": global_root,
        "global_modules_sha256": runtime_hashes["global"],
    }


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
    system_runtime_before = _system_runtime_snapshot(distribution)
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
    rollback_lock: ProcedureNpmDependencyLock | None = None
    other_procedure_lock: ProcedureNpmDependencyLock | None = None
    graph_lock: ProcedureNpmDependencyLock | None = None
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
        selected_link = f"__xaa_selected_skill__/{lock.skill_id}/v1/node_modules"
        script = (
            "const fs=require('fs'),value=require('xaa-offline-fixture');"
            f"if(fs.realpathSync('{selected_link}')!=='/opt/xaa/procedure-node/node_modules')process.exit(8);"
            "if(fs.existsSync('node_modules'))process.exit(9);"
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
        other_archive = _tarball(module_value="other-procedure")
        other_integrity = "sha512-" + base64.b64encode(
            hashlib.sha512(other_archive).digest()
        ).decode("ascii")
        other_package_lock_document = json.loads(package_lock)
        other_package_lock_document["packages"]["node_modules/xaa-offline-fixture"]["integrity"] = (
            other_integrity
        )
        other_package_lock = json.dumps(other_package_lock_document, separators=(",", ":")).encode()
        other_procedure_lock = lock.model_copy(
            update={
                "skill_id": str(uuid4()),
                "source_sha256": "c" * 64,
                "lockfile_sha256": hashlib.sha256(other_package_lock).hexdigest(),
                "packages": (
                    NpmLockedPackage(
                        package_path="node_modules/xaa-offline-fixture",
                        name="xaa-offline-fixture",
                        version="1.0.0",
                        resolved_url=resolved,
                        integrity=other_integrity,
                    ),
                ),
            }
        )
        other_archive_path = report_path.parent / "other-procedure-fixture.tgz"
        other_archive_path.write_bytes(other_archive)
        store.store_files(
            other_procedure_lock,
            {other_procedure_lock.packages[0].package_path: other_archive_path},
        )
        other_directory = store.root / other_procedure_lock.environment_digest()
        other_installed = await transport.install_node_environment(
            other_procedure_lock,
            package_manifest,
            other_package_lock,
            other_directory,
            deadline=time.monotonic() + 300,
        )
        if (
            other_installed.get("installed") is not True
            or other_procedure_lock.skill_id == lock.skill_id
            or other_procedure_lock.environment_digest() == lock.environment_digest()
        ):
            raise RuntimeError("A second procedure's distinct npm environment was not installed.")
        other_script, other_snapshot = _procedure_probe(other_procedure_lock, "other-procedure")
        other_intent = intent.model_copy(
            update={
                "argv": ["node", other_script],
                "execution_id": uuid.uuid4().hex,
                "procedure_node_environment_digest": other_procedure_lock.environment_digest(),
                "procedure_node_environment_tree_sha256": other_installed["tree_sha256"],
                "procedure_skill_id": other_procedure_lock.skill_id,
                "procedure_version": other_procedure_lock.skill_version,
            }
        )
        original_intent = intent.model_copy(update={"execution_id": uuid.uuid4().hex})
        original_procedure_run = asyncio.create_task(
            transport.run(original_intent, snapshot, deadline=time.monotonic() + 30)
        )
        other_procedure_run = asyncio.create_task(
            transport.run(other_intent, other_snapshot, deadline=time.monotonic() + 30)
        )
        await asyncio.sleep(2)
        cross_procedure_removal_refusals = 0
        for active_digest in (lock.environment_digest(), other_procedure_lock.environment_digest()):
            try:
                await transport.remove_node_environment(
                    active_digest, deadline=time.monotonic() + 20
                )
            except EnvironmentInUseError:
                cross_procedure_removal_refusals += 1
        original_result, other_result = await asyncio.gather(
            original_procedure_run, other_procedure_run
        )
        if (
            original_result.get("succeeded") is not True
            or original_result.get("stdout_b64")
            != base64.b64encode(b"offline-ready").decode("ascii")
            or original_result.get("procedure_node_environment_digest") != lock.environment_digest()
            or original_result.get("cleanup_confirmed") is not True
            or other_result.get("succeeded") is not True
            or other_result.get("stdout_b64")
            != base64.b64encode(b"other-procedure").decode("ascii")
            or other_result.get("procedure_node_environment_digest")
            != other_procedure_lock.environment_digest()
            or other_result.get("cleanup_confirmed") is not True
            or cross_procedure_removal_refusals != 2
        ):
            raise RuntimeError("Concurrent distinct-procedure npm isolation failed.")
        other_removed = await transport.remove_node_environment(
            other_procedure_lock.environment_digest(), deadline=time.monotonic() + 30
        )
        other_after = await transport.node_environment_status(
            other_procedure_lock.environment_digest(), deadline=time.monotonic() + 30
        )
        original_after_other_removal = await transport.node_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if (
            other_removed.get("removed") is not True
            or other_after.get("installed") is not False
            or original_after_other_removal.get("installed") is not True
        ):
            raise RuntimeError("Removing one procedure's npm environment affected another.")
        scoped_name = "@xaa/fixture-plugin"
        transitive_name = "xaa-transitive-fixture"
        peer_name = "xaa-peer-fixture"
        optional_name = "xaa-optional-fixture"
        windows_optional_name = "xaa-windows-optional-fixture"
        optional_peer_name = "xaa-optional-peer-fixture"
        scoped_url = "https://registry.npmjs.org/@xaa/fixture-plugin/-/fixture-plugin-1.0.0.tgz"
        transitive_url = (
            "https://registry.npmjs.org/xaa-transitive-fixture/-/xaa-transitive-fixture-1.0.0.tgz"
        )
        peer_url = "https://registry.npmjs.org/xaa-peer-fixture/-/xaa-peer-fixture-1.0.0.tgz"
        optional_url = (
            "https://registry.npmjs.org/xaa-optional-fixture/-/xaa-optional-fixture-1.0.0.tgz"
        )
        windows_optional_url = (
            "https://registry.npmjs.org/xaa-windows-optional-fixture/-/"
            "xaa-windows-optional-fixture-1.0.0.tgz"
        )
        scoped_archive = _package_tarball(
            {
                "name": scoped_name,
                "version": "1.0.0",
                "main": "index.js",
                "dependencies": {transitive_name: "^1.0.0"},
                "peerDependencies": {
                    peer_name: "^1.0.0",
                    optional_peer_name: "^1.0.0",
                },
                "peerDependenciesMeta": {optional_peer_name: {"optional": True}},
            },
            {
                "index.js": (
                    b"module.exports = require('xaa-transitive-fixture') + '-' + "
                    b"require('xaa-peer-fixture') + '-scoped';\n"
                )
            },
        )
        transitive_archive = _package_tarball(
            {"name": transitive_name, "version": "1.0.0", "main": "index.js"},
            {"index.js": b"module.exports = 'offline-transitive';\n"},
        )
        peer_archive = _package_tarball(
            {"name": peer_name, "version": "1.0.0", "main": "index.js"},
            {"index.js": b"module.exports = 'required-peer';\n"},
        )
        optional_archive = _package_tarball(
            {"name": optional_name, "version": "1.0.0", "main": "index.js"},
            {"index.js": b"module.exports = 'optional-root';\n"},
        )
        windows_optional_archive = _package_tarball(
            {
                "name": windows_optional_name,
                "version": "1.0.0",
                "main": "index.js",
                "os": ["win32"],
            },
            {"index.js": b"module.exports = 'windows-only';\n"},
        )
        scoped_integrity = "sha512-" + base64.b64encode(
            hashlib.sha512(scoped_archive).digest()
        ).decode("ascii")
        transitive_integrity = "sha512-" + base64.b64encode(
            hashlib.sha512(transitive_archive).digest()
        ).decode("ascii")
        peer_integrity = "sha512-" + base64.b64encode(hashlib.sha512(peer_archive).digest()).decode(
            "ascii"
        )
        optional_integrity = "sha512-" + base64.b64encode(
            hashlib.sha512(optional_archive).digest()
        ).decode("ascii")
        windows_optional_integrity = "sha512-" + base64.b64encode(
            hashlib.sha512(windows_optional_archive).digest()
        ).decode("ascii")
        graph_manifest = json.dumps(
            {
                "name": "xaa-probe-transitive-project",
                "version": "1.0.0",
                "dependencies": {scoped_name: "^1.0.0"},
                "optionalDependencies": {
                    optional_name: "^1.0.0",
                    windows_optional_name: "^1.0.0",
                },
            },
            separators=(",", ":"),
        ).encode()
        graph_lock_document = {
            "name": "xaa-probe-transitive-project",
            "version": "1.0.0",
            "lockfileVersion": 3,
            "requires": True,
            "packages": {
                "": {
                    "name": "xaa-probe-transitive-project",
                    "version": "1.0.0",
                    "dependencies": {scoped_name: "^1.0.0"},
                    "optionalDependencies": {
                        optional_name: "^1.0.0",
                        windows_optional_name: "^1.0.0",
                    },
                },
                "node_modules/@xaa/fixture-plugin": {
                    "version": "1.0.0",
                    "resolved": scoped_url,
                    "integrity": scoped_integrity,
                    "dependencies": {transitive_name: "^1.0.0"},
                    "peerDependencies": {
                        peer_name: "^1.0.0",
                        optional_peer_name: "^1.0.0",
                    },
                    "peerDependenciesMeta": {optional_peer_name: {"optional": True}},
                },
                "node_modules/@xaa/fixture-plugin/node_modules/xaa-transitive-fixture": {
                    "version": "1.0.0",
                    "resolved": transitive_url,
                    "integrity": transitive_integrity,
                },
                f"node_modules/{peer_name}": {
                    "version": "1.0.0",
                    "resolved": peer_url,
                    "integrity": peer_integrity,
                    "peer": True,
                },
                f"node_modules/{optional_name}": {
                    "version": "1.0.0",
                    "resolved": optional_url,
                    "integrity": optional_integrity,
                    "optional": True,
                },
                f"node_modules/{windows_optional_name}": {
                    "version": "1.0.0",
                    "resolved": windows_optional_url,
                    "integrity": windows_optional_integrity,
                    "optional": True,
                    "os": ["win32"],
                },
            },
        }
        graph_package_lock = json.dumps(graph_lock_document, separators=(",", ":")).encode()
        graph_lock = ProcedureNpmDependencyLock(
            schema_version=1,
            skill_id=str(uuid4()),
            skill_version=1,
            source_sha256="d" * 64,
            package_manifest_sha256=hashlib.sha256(graph_manifest).hexdigest(),
            lockfile_sha256=hashlib.sha256(graph_package_lock).hexdigest(),
            distribution=distribution,
            node_version=node_version,
            npm_version=npm_version,
            requirements=(
                f"{scoped_name}@^1.0.0",
                f"{optional_name}@^1.0.0",
                f"{windows_optional_name}@^1.0.0",
            ),
            root_dependencies=(
                NpmRootDependency(name=scoped_name, version_range="^1.0.0", kind="dependencies"),
                NpmRootDependency(
                    name=optional_name, version_range="^1.0.0", kind="optionalDependencies"
                ),
                NpmRootDependency(
                    name=windows_optional_name,
                    version_range="^1.0.0",
                    kind="optionalDependencies",
                ),
            ),
            packages=(
                NpmLockedPackage(
                    package_path="node_modules/@xaa/fixture-plugin",
                    name=scoped_name,
                    version="1.0.0",
                    resolved_url=scoped_url,
                    integrity=scoped_integrity,
                ),
                NpmLockedPackage(
                    package_path=(
                        "node_modules/@xaa/fixture-plugin/node_modules/xaa-transitive-fixture"
                    ),
                    name=transitive_name,
                    version="1.0.0",
                    resolved_url=transitive_url,
                    integrity=transitive_integrity,
                ),
                NpmLockedPackage(
                    package_path=f"node_modules/{peer_name}",
                    name=peer_name,
                    version="1.0.0",
                    resolved_url=peer_url,
                    integrity=peer_integrity,
                ),
                NpmLockedPackage(
                    package_path=f"node_modules/{optional_name}",
                    name=optional_name,
                    version="1.0.0",
                    resolved_url=optional_url,
                    integrity=optional_integrity,
                ),
                NpmLockedPackage(
                    package_path=f"node_modules/{windows_optional_name}",
                    name=windows_optional_name,
                    version="1.0.0",
                    resolved_url=windows_optional_url,
                    integrity=windows_optional_integrity,
                ),
            ),
        )
        scoped_archive_path = report_path.parent / "scoped-fixture.tgz"
        transitive_archive_path = report_path.parent / "transitive-fixture.tgz"
        peer_archive_path = report_path.parent / "peer-fixture.tgz"
        optional_archive_path = report_path.parent / "optional-fixture.tgz"
        windows_optional_archive_path = report_path.parent / "windows-optional-fixture.tgz"
        scoped_archive_path.write_bytes(scoped_archive)
        transitive_archive_path.write_bytes(transitive_archive)
        peer_archive_path.write_bytes(peer_archive)
        optional_archive_path.write_bytes(optional_archive)
        windows_optional_archive_path.write_bytes(windows_optional_archive)
        store.store_files(
            graph_lock,
            {
                package.package_path: archive_path
                for package, archive_path in zip(
                    graph_lock.packages,
                    (
                        scoped_archive_path,
                        transitive_archive_path,
                        peer_archive_path,
                        optional_archive_path,
                        windows_optional_archive_path,
                    ),
                    strict=True,
                )
            },
        )
        graph_installed = await transport.install_node_environment(
            graph_lock,
            graph_manifest,
            graph_package_lock,
            store.root / graph_lock.environment_digest(),
            deadline=time.monotonic() + 300,
        )
        graph_script_path = f"__xaa_selected_skill__/{graph_lock.skill_id}/v1/check.cjs"
        graph_procedure_path = f"__xaa_selected_skill__/{graph_lock.skill_id}/v1"
        graph_script = (
            "const fs=require('fs'),value=require('@xaa/fixture-plugin');"
            "if(value!=='offline-transitive-required-peer-scoped')process.exit(10);"
            f"if(require('{optional_name}')!=='optional-root')process.exit(11);"
            f"try{{require.resolve('{optional_peer_name}');process.exit(12);}}"
            "catch(error){if(error.code!=='MODULE_NOT_FOUND')process.exit(13);}"
            f"try{{require.resolve('{windows_optional_name}');process.exit(14);}}"
            "catch(error){if(error.code!=='MODULE_NOT_FOUND')process.exit(15);}"
            f"if(fs.realpathSync('{graph_procedure_path}/node_modules')!=="
            "'/opt/xaa/procedure-node/node_modules')process.exit(8);"
            "if(fs.existsSync('node_modules'))process.exit(9);"
            "process.stdout.write(value);"
        ).encode("ascii")
        graph_snapshot = WorkspaceSnapshot(
            files=(SnapshotFile(graph_script_path, graph_script),),
            directories=(
                "__xaa_selected_skill__",
                f"__xaa_selected_skill__/{graph_lock.skill_id}",
                graph_procedure_path,
            ),
        )
        graph_intent = intent.model_copy(
            update={
                "argv": ["node", graph_script_path],
                "execution_id": uuid.uuid4().hex,
                "max_seconds": 10,
                "procedure_node_environment_digest": graph_lock.environment_digest(),
                "procedure_node_environment_tree_sha256": graph_installed["tree_sha256"],
                "procedure_skill_id": graph_lock.skill_id,
                "procedure_version": graph_lock.skill_version,
            }
        )
        graph_result = await transport.run(
            graph_intent, graph_snapshot, deadline=time.monotonic() + 30
        )
        if (
            graph_installed.get("installed") is not True
            or graph_result.get("succeeded") is not True
            or graph_result.get("stdout_b64")
            != base64.b64encode(b"offline-transitive-required-peer-scoped").decode("ascii")
            or graph_result.get("procedure_node_environment_digest")
            != graph_lock.environment_digest()
            or graph_result.get("cleanup_confirmed") is not True
        ):
            raise RuntimeError(
                "Scoped npm package with a nested transitive dependency failed in WSL."
            )
        graph_removed = await transport.remove_node_environment(
            graph_lock.environment_digest(), deadline=time.monotonic() + 30
        )
        graph_after = await transport.node_environment_status(
            graph_lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if graph_removed.get("removed") is not True or graph_after.get("installed") is not False:
            raise RuntimeError("Exact cleanup of the scoped transitive npm environment failed.")
        rollback_archive = _tarball(version="1.1.0", module_value="new-version")
        rollback_integrity = "sha512-" + base64.b64encode(
            hashlib.sha512(rollback_archive).digest()
        ).decode("ascii")
        rollback_resolved = (
            "https://registry.npmjs.org/xaa-offline-fixture/-/xaa-offline-fixture-1.1.0.tgz"
        )
        rollback_manifest = package_manifest.replace(b"^1.0.0", b"^1.1.0")
        rollback_lock_document = json.loads(package_lock)
        rollback_package_record = rollback_lock_document["packages"][
            "node_modules/xaa-offline-fixture"
        ]
        rollback_package_record["version"] = "1.1.0"
        rollback_package_record["resolved"] = rollback_resolved
        rollback_package_record["integrity"] = rollback_integrity
        rollback_lock_document["packages"][""]["dependencies"]["xaa-offline-fixture"] = "^1.1.0"
        rollback_package_lock = json.dumps(rollback_lock_document, separators=(",", ":")).encode()
        rollback_lock = lock.model_copy(
            update={
                "skill_version": 2,
                "source_sha256": "b" * 64,
                "package_manifest_sha256": hashlib.sha256(rollback_manifest).hexdigest(),
                "lockfile_sha256": hashlib.sha256(rollback_package_lock).hexdigest(),
                "requirements": ("xaa-offline-fixture@^1.1.0",),
                "root_dependencies": (
                    NpmRootDependency(
                        name="xaa-offline-fixture",
                        version_range="^1.1.0",
                        kind="dependencies",
                    ),
                ),
                "packages": (
                    NpmLockedPackage(
                        package_path="node_modules/xaa-offline-fixture",
                        name="xaa-offline-fixture",
                        version="1.1.0",
                        resolved_url=rollback_resolved,
                        integrity=rollback_integrity,
                    ),
                ),
            }
        )
        rollback_archive_path = report_path.parent / "fixture-v2.tgz"
        rollback_archive_path.write_bytes(rollback_archive)
        store.store_files(
            rollback_lock,
            {rollback_lock.packages[0].package_path: rollback_archive_path},
        )
        rollback_directory = store.root / rollback_lock.environment_digest()
        newer = await transport.install_node_environment(
            rollback_lock,
            rollback_manifest,
            rollback_package_lock,
            rollback_directory,
            deadline=time.monotonic() + 300,
        )
        old_before_rollback = await transport.node_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if (
            newer.get("installed") is not True
            or rollback_lock.environment_digest() == lock.environment_digest()
            or old_before_rollback.get("installed") is not True
        ):
            raise RuntimeError("Installing the newer procedure version did not preserve v1.")
        newer_script, newer_snapshot = _procedure_probe(rollback_lock, "new-version")
        newer_intent = intent.model_copy(
            update={
                "argv": ["node", newer_script],
                "execution_id": uuid.uuid4().hex,
                "procedure_node_environment_digest": rollback_lock.environment_digest(),
                "procedure_node_environment_tree_sha256": newer["tree_sha256"],
                "procedure_version": rollback_lock.skill_version,
            }
        )
        rollback_intent = intent.model_copy(update={"execution_id": uuid.uuid4().hex})
        older_run = asyncio.create_task(
            transport.run(rollback_intent, snapshot, deadline=time.monotonic() + 30)
        )
        newer_run = asyncio.create_task(
            transport.run(newer_intent, newer_snapshot, deadline=time.monotonic() + 30)
        )
        await asyncio.sleep(2)
        active_environment_refusals = 0
        for active_digest in (lock.environment_digest(), rollback_lock.environment_digest()):
            try:
                await transport.remove_node_environment(
                    active_digest, deadline=time.monotonic() + 20
                )
            except EnvironmentInUseError:
                active_environment_refusals += 1
        rolled_back, newer_run_result = await asyncio.gather(older_run, newer_run)
        if (
            rolled_back.get("succeeded") is not True
            or rolled_back.get("stdout_b64") != base64.b64encode(b"offline-ready").decode("ascii")
            or rolled_back.get("procedure_node_environment_digest") != lock.environment_digest()
            or rolled_back.get("cleanup_confirmed") is not True
            or newer_run_result.get("succeeded") is not True
            or newer_run_result.get("stdout_b64")
            != base64.b64encode(b"new-version").decode("ascii")
            or newer_run_result.get("procedure_node_environment_digest")
            != rollback_lock.environment_digest()
            or newer_run_result.get("cleanup_confirmed") is not True
            or active_environment_refusals != 2
        ):
            raise RuntimeError(
                "Concurrent procedure isolation or rollback to the previous Node digest failed."
            )
        newer_removed = await transport.remove_node_environment(
            rollback_lock.environment_digest(), deadline=time.monotonic() + 30
        )
        newer_after = await transport.node_environment_status(
            rollback_lock.environment_digest(), deadline=time.monotonic() + 30
        )
        old_after_newer_removal = await transport.node_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if (
            newer_removed.get("removed") is not True
            or newer_after.get("installed") is not False
            or old_after_newer_removal.get("installed") is not True
        ):
            raise RuntimeError("Removing v2 affected the retained v1 Node environment.")
        removed = await transport.remove_node_environment(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        after = await transport.node_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if removed.get("removed") is not True or after.get("installed") is not False:
            raise RuntimeError("Exact npm environment cleanup failed.")
        system_runtime_after = _system_runtime_snapshot(distribution)
        if system_runtime_after != system_runtime_before:
            raise RuntimeError(
                "The system Node/npm runtime changed during the managed install test."
            )
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
            "selected_node_modules_scoped_to_procedure": True,
            "workspace_node_modules_absent": True,
            "system_runtime_unchanged": True,
            "system_runtime": system_runtime_after,
            "newer_procedure_version_installed": True,
            "rollback_to_older_digest_succeeded": True,
            "newer_digest_removal_preserved_older": True,
            "concurrent_procedure_versions_isolated": True,
            "concurrent_distinct_procedures_isolated": True,
            "cross_procedure_active_removal_refused": True,
            "cross_procedure_exact_removal_preserved_neighbor": True,
            "scoped_package_installed_offline": True,
            "nested_transitive_dependency_loaded_in_procedure": True,
            "required_peer_dependency_loaded": True,
            "root_optional_dependency_loaded": True,
            "optional_peer_dependency_absence_accepted": True,
            "incompatible_platform_optional_dependency_skipped": True,
            "transitive_environment_removed_exactly": True,
            "both_active_environments_refused_removal": True,
            "integrity_status": True,
            "exact_environment_removed": True,
            "network": "none",
            "cleanup_confirmed": True,
        }
    finally:
        if rollback_lock is not None:
            await transport.remove_node_environment(
                rollback_lock.environment_digest(), deadline=time.monotonic() + 30
            )
        if other_procedure_lock is not None:
            await transport.remove_node_environment(
                other_procedure_lock.environment_digest(), deadline=time.monotonic() + 30
            )
        if graph_lock is not None:
            await transport.remove_node_environment(
                graph_lock.environment_digest(), deadline=time.monotonic() + 30
            )
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

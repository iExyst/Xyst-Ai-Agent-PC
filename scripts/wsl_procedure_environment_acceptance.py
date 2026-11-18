"""Exercise the managed WSL installer with a local two-wheel lock fixture."""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from xaa.extensions.procedure_dependencies import (
    LockedPythonWheel,
    ProcedurePythonDependencyLock,
    PythonDependencyTarget,
)
from xaa.extensions.wheelhouse_storage import ProcedureWheelhouseStore
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.owned_transport import EnvironmentInUseError, TransportError
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport


def build_wheel(
    name: str,
    value: str,
    requires_dist: str | None = None,
    *,
    version: str = "1.0",
    extra_file_count: int = 0,
) -> tuple[str, bytes]:
    normalized = name.replace("-", "_")
    dist_info = f"{normalized}-{version}.dist-info"
    files = {
        f"{normalized}/__init__.py": f"VALUE = {value!r}\n".encode(),
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            + (f"Requires-Dist: {requires_dist}\n" if requires_dist else "")
            + "\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: xaa-acceptance\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n\n"
        ),
    }
    for index in range(extra_file_count):
        files[f"{normalized}/recovery-fixture/{index:05d}.txt"] = b"fixture\n"
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for path, content in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow((path, f"sha256={digest}", len(content)))
    writer.writerow((f"{dist_info}/RECORD", "", ""))
    files[f"{dist_info}/RECORD"] = record.getvalue().encode("utf-8")
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return f"{normalized}-{version}-py3-none-any.whl", output.getvalue()


def _wsl_binary() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe")


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
    target = PurePosixPath("/var/lib/xaa-procedure-environments") / digest
    unit = f"xaa-procenv-{digest[:32]}.service"
    _wsl(
        distribution,
        "sudo",
        "install",
        "-d",
        "-m",
        "0700",
        "--",
        str(target.parent),
    )
    _wsl(
        distribution,
        "sudo",
        "install",
        "-d",
        "--",
        str(target / "environment/venv"),
    )
    _wsl(distribution, "sudo", "touch", "--", str(target / "environment/venv/.partial"))
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
        if _wsl(distribution, "sudo", "systemctl", "is-active", unit) == "active":
            return
        time.sleep(0.1)
    raise RuntimeError("Could not seed the active interrupted Python-install fixture unit.")


def _system_python_snapshot(distribution: str) -> dict[str, Any]:
    script = (
        "import importlib.util,json,sys,sysconfig\n"
        "names=('xaa_top','xaa_leaf','xaa_isolation_probe')\n"
        "print(json.dumps({'version':sys.version,'executable':sys.executable,"
        "'prefix':sys.prefix,'base_prefix':sys.base_prefix,'paths':sysconfig.get_paths(),"
        "'modules':{name:(spec.origin if (spec:=importlib.util.find_spec(name)) else None) "
        "for name in names}},sort_keys=True))"
    )
    return json.loads(_wsl(distribution, "python3", "-c", script))


async def _kill_and_retry_procedure_install(
    distribution: str,
    transport: WSLTransport,
    lock: ProcedurePythonDependencyLock,
    wheelhouse: Path,
) -> dict[str, Any]:
    digest = lock.environment_digest()
    unit = f"xaa-procenv-{digest[:32]}.service"
    install_task = asyncio.create_task(
        transport.install_procedure_environment(
            lock, wheelhouse, deadline=time.monotonic() + 300
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
        raise RuntimeError("Could not interrupt the live Python installation service.")
    try:
        await install_task
    except TransportError:
        pass
    else:
        raise RuntimeError("The Python installer completed despite the requested process kill.")
    status = await transport.procedure_environment_status(
        digest, deadline=time.monotonic() + 30
    )
    root = PurePosixPath("/var/lib/xaa-procedure-environments") / digest
    target_state = _wsl(
        distribution,
        "sudo",
        "bash",
        "-lc",
        'if test -e "$1"; then echo present; else echo absent; fi',
        "xaa",
        str(root),
    )
    if status.get("installed") is not False or target_state != "absent":
        raise RuntimeError("Killed Python install left a published or partial environment.")
    recovered = await transport.install_procedure_environment(
        lock, wheelhouse, deadline=time.monotonic() + 300
    )
    if (
        recovered.get("installed") is not True
        or recovered.get("environment_digest") != digest
    ):
        raise RuntimeError("Retry after killing Python install did not recover the environment.")
    return recovered


def make_lock(
    distribution: str,
    artifacts: dict[str, bytes],
    *,
    requirements: tuple[str, ...] = ("xaa-top==1.0",),
    skill_version: int = 1,
    source_sha256: str = "a" * 64,
) -> ProcedurePythonDependencyLock:
    return ProcedurePythonDependencyLock(
        schema_version=1,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=skill_version,
        source_sha256=source_sha256,
        target=PythonDependencyTarget(
            distribution=distribution,
            environment={
                "implementation_name": "cpython",
                "implementation_version": "3.12.3",
                "os_name": "posix",
                "platform_machine": "x86_64",
                "platform_python_implementation": "CPython",
                "platform_release": "6.8.0-wsl",
                "platform_system": "Linux",
                "platform_version": "fixture",
                "python_full_version": "3.12.3",
                "python_version": "3.12",
                "sys_platform": "linux",
            },
            compatible_wheel_tags=("py3-none-any",),
        ),
        requirements=requirements,
        wheels=tuple(
            LockedPythonWheel(
                filename=filename,
                project_name=filename.split("-", 1)[0].replace("_", "-"),
                version=filename.split("-")[1],
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                requires_dist=("xaa-leaf==1.0",) if filename.startswith("xaa_top-") else (),
            )
            for filename, content in sorted(artifacts.items())
        ),
    )


async def run(distribution: str) -> dict[str, Any]:
    leaf_name, leaf_bytes = build_wheel("xaa-leaf", "leaf-ready")
    top_name, top_bytes = build_wheel(
        "xaa-top", "top-ready", "xaa-leaf==1.0", extra_file_count=4096
    )
    artifacts = {leaf_name: leaf_bytes, top_name: top_bytes}
    lock = make_lock(distribution, artifacts)
    isolated_name, isolated_bytes = build_wheel("xaa-isolation-probe", "other-environment-only")
    isolated_artifacts = {isolated_name: isolated_bytes}
    isolated_lock = make_lock(
        distribution,
        isolated_artifacts,
        requirements=("xaa-isolation-probe==1.0",),
    )
    with tempfile.TemporaryDirectory(prefix="xaa-procedure-environment-") as temporary:
        store = ProcedureWheelhouseStore(Path(temporary) / "wheelhouses")
        store.store(lock, artifacts)
        store.store(isolated_lock, isolated_artifacts)
        transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
        system_python_before = _system_python_snapshot(distribution)
        _seed_interrupted_install(distribution, lock.environment_digest())
        installed = await transport.install_procedure_environment(
            lock, store.root / lock.environment_digest(), deadline=time.monotonic() + 180
        )
        repeated = await transport.install_procedure_environment(
            lock, store.root / lock.environment_digest(), deadline=time.monotonic() + 60
        )
        isolated_installed = await transport.install_procedure_environment(
            isolated_lock,
            store.root / isolated_lock.environment_digest(),
            deadline=time.monotonic() + 180,
        )
        observed = await transport.procedure_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        interrupted_unit = f"xaa-procenv-{lock.environment_digest()[:32]}.service"
        interrupted_unit_state = _wsl(
            distribution,
            "sudo",
            "systemctl",
            "show",
            "--property=ActiveState",
            "--value",
            interrupted_unit,
        )
        interrupted_partial_state = _wsl(
            distribution,
            "sudo",
            "bash",
            "-lc",
            'if test -e "$1"; then echo present; else echo absent; fi',
            "xaa",
            str(
                PurePosixPath("/var/lib/xaa-procedure-environments")
                / lock.environment_digest()
                / "environment/venv/.partial"
            ),
        )
        if (
            installed.get("installed") is not True
            or installed.get("environment_digest") != lock.environment_digest()
            or installed.get("tree_sha256") != repeated.get("tree_sha256")
            or observed.get("tree_sha256") != installed.get("tree_sha256")
            or observed.get("lock_sha256")
            != hashlib.sha256(lock.canonical_json().encode()).hexdigest()
            or observed.get("installed_bytes", 0) <= 0
            or isolated_installed.get("installed") is not True
            or isolated_installed.get("environment_digest") != isolated_lock.environment_digest()
            or interrupted_unit_state != "inactive"
            or interrupted_partial_state != "absent"
        ):
            raise RuntimeError(
                "Managed WSL environment install, integrity, or interrupted-install recovery failed."
            )
        lease_started = asyncio.Event()

        async def capture_lease_start(stream: str, chunk: bytes) -> None:
            if stream == "stdout" and b"LEASE_OPEN" in chunk:
                lease_started.set()

        lease_run = asyncio.create_task(
            transport.run(
                CommandIntent(
                    argv=[
                        "/opt/xaa/procedure-python/bin/python",
                        "-I",
                        "-c",
                        "import time; print('LEASE_OPEN', flush=True); time.sleep(3)",
                    ],
                    max_seconds=10,
                    execution_id=uuid4().hex,
                    backend="wsl-bubblewrap-v1",
                    distribution=distribution,
                    profile_sha256=transport.profile_sha256,
                    workspace="procedure-environment-lease-acceptance",
                    procedure_environment_digest=lock.environment_digest(),
                    procedure_environment_tree_sha256=installed["tree_sha256"],
                ),
                WorkspaceSnapshot((), (), ()),
                deadline=time.monotonic() + 30,
                on_output=capture_lease_start,
            )
        )
        try:
            await asyncio.wait_for(lease_started.wait(), timeout=15)
        except TimeoutError as exc:
            await lease_run
            raise RuntimeError("The leased procedure did not start in time.") from exc
        try:
            await transport.remove_procedure_environment(
                lock.environment_digest(), deadline=time.monotonic() + 30
            )
        except EnvironmentInUseError:
            active_removal_refused = True
        else:
            active_removal_refused = False
        active_run_receipt = await lease_run
        if not active_removal_refused or active_run_receipt.get("succeeded") is not True:
            raise RuntimeError("Environment removal was not fenced by the active execution lease.")
        if not await transport.remove_procedure_environment(
            lock.environment_digest(), deadline=time.monotonic() + 30
        ):
            raise RuntimeError("Could not remove the first Python fixture before crash recovery.")
        installed = await _kill_and_retry_procedure_install(
            distribution, transport, lock, store.root / lock.environment_digest()
        )
        isolation = await transport.run(
            CommandIntent(
                argv=[
                    "/opt/xaa/procedure-python/bin/python",
                    "-I",
                    "-c",
                    (
                        "import pathlib\n"
                        f"other=pathlib.Path('/var/lib/xaa-procedure-environments/{isolated_lock.environment_digest()}/environment/venv')\n"
                        "print('other-environment-visible', other.exists())\n"
                        "assert not other.exists()\n"
                        "try:\n import xaa_isolation_probe\n"
                        "except ModuleNotFoundError:\n print('cross-environment-import-blocked')\n"
                        "else:\n raise SystemExit('another procedure environment was importable')\n"
                    ),
                ],
                max_seconds=10,
                execution_id=uuid4().hex,
                backend="wsl-bubblewrap-v1",
                distribution=distribution,
                profile_sha256=transport.profile_sha256,
                workspace="procedure-environment-isolation-acceptance",
                procedure_environment_digest=lock.environment_digest(),
                procedure_environment_tree_sha256=installed["tree_sha256"],
            ),
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + 30,
        )
        isolation_output = base64.b64decode(isolation["stdout_b64"], validate=True).decode("utf-8")
        if (
            isolation.get("succeeded") is not True
            or "other-environment-visible False" not in isolation_output
            or "cross-environment-import-blocked" not in isolation_output
        ):
            raise RuntimeError(
                "The selected procedure could access another installed Python environment: "
                f"receipt={isolation!r}; stdout={isolation_output!r}"
            )
        execution = await transport.run(
            CommandIntent(
                argv=[
                    "/opt/xaa/procedure-python/bin/python",
                    "-I",
                    "-c",
                    (
                        "import pathlib,xaa_top,xaa_leaf; "
                        "print(xaa_top.VALUE, xaa_leaf.VALUE, xaa_top.__file__); "
                        "pathlib.Path(xaa_top.__file__).write_text('must-not-write')"
                    ),
                ],
                max_seconds=10,
                execution_id=uuid4().hex,
                backend="wsl-bubblewrap-v1",
                distribution=distribution,
                profile_sha256=transport.profile_sha256,
                workspace="procedure-environment-acceptance",
                procedure_environment_digest=lock.environment_digest(),
                procedure_environment_tree_sha256=installed["tree_sha256"],
            ),
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + 30,
        )
        output = base64.b64decode(execution["stdout_b64"], validate=True).decode("utf-8")
        if (
            execution.get("succeeded") is not False
            or "top-ready leaf-ready /opt/xaa/procedure-python/" not in output
            or "Read-only file system"
            not in base64.b64decode(execution["stderr_b64"], validate=True).decode("utf-8")
            or execution.get("cleanup_confirmed") is not True
        ):
            stderr = base64.b64decode(execution["stderr_b64"], validate=True).decode("utf-8")
            await transport.remove_procedure_environment(
                lock.environment_digest(), deadline=time.monotonic() + 30
            )
            await transport.remove_procedure_environment(
                isolated_lock.environment_digest(), deadline=time.monotonic() + 30
            )
            raise RuntimeError(
                "WSL did not execute the selected environment read-only: "
                f"receipt={execution!r}; stdout={output!r}; stderr={stderr!r}"
            )

        revision_top_name, revision_top_bytes = build_wheel(
            "xaa-top", "top-revision-two", "xaa-leaf==1.0", version="1.1"
        )
        revision_artifacts = {leaf_name: leaf_bytes, revision_top_name: revision_top_bytes}
        revision_lock = make_lock(
            distribution,
            revision_artifacts,
            requirements=("xaa-top==1.1",),
            skill_version=2,
            source_sha256="c" * 64,
        )
        store.store(revision_lock, revision_artifacts)
        revision_install = await transport.install_procedure_environment(
            revision_lock,
            store.root / revision_lock.environment_digest(),
            deadline=time.monotonic() + 180,
        )
        if (
            revision_lock.environment_digest() == lock.environment_digest()
            or revision_install.get("installed") is not True
            or (
                await transport.procedure_environment_status(
                    lock.environment_digest(), deadline=time.monotonic() + 30
                )
            ).get("installed")
            is not True
        ):
            raise RuntimeError(
                "Installing a new procedure revision replaced its older environment."
            )

        async def run_revision(
            revision: ProcedurePythonDependencyLock,
            tree_sha256: str,
            workspace: str,
        ) -> dict[str, Any]:
            return await transport.run(
                CommandIntent(
                    argv=[
                        "/opt/xaa/procedure-python/bin/python",
                        "-I",
                        "-c",
                        "import xaa_top; print(xaa_top.VALUE)",
                    ],
                    max_seconds=10,
                    execution_id=uuid4().hex,
                    backend="wsl-bubblewrap-v1",
                    distribution=distribution,
                    profile_sha256=transport.profile_sha256,
                    workspace=workspace,
                    procedure_environment_digest=revision.environment_digest(),
                    procedure_environment_tree_sha256=tree_sha256,
                ),
                WorkspaceSnapshot((), (), ()),
                deadline=time.monotonic() + 30,
            )

        revision_execution = await run_revision(
            revision_lock, revision_install["tree_sha256"], "procedure-environment-v2"
        )
        rollback_execution = await run_revision(
            lock, installed["tree_sha256"], "procedure-environment-v1-rollback"
        )
        if (
            revision_execution.get("succeeded") is not True
            or base64.b64decode(revision_execution["stdout_b64"], validate=True).decode().strip()
            != "top-revision-two"
            or revision_execution.get("procedure_environment_digest")
            != revision_lock.environment_digest()
            or revision_execution.get("cleanup_confirmed") is not True
            or rollback_execution.get("succeeded") is not True
            or base64.b64decode(rollback_execution["stdout_b64"], validate=True).decode().strip()
            != "top-ready"
            or rollback_execution.get("procedure_environment_digest") != lock.environment_digest()
            or rollback_execution.get("cleanup_confirmed") is not True
        ):
            raise RuntimeError("Immutable procedure revision selection or rollback failed.")
        revision_removed = await transport.remove_procedure_environment(
            revision_lock.environment_digest(), deadline=time.monotonic() + 30
        )
        old_after_revision_removal = await transport.procedure_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if (
            revision_removed.get("removed") is not True
            or old_after_revision_removal.get("installed") is not True
        ):
            raise RuntimeError("Removing the new procedure revision changed the retained old one.")
        removed = await transport.remove_procedure_environment(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        isolated_removed = await transport.remove_procedure_environment(
            isolated_lock.environment_digest(), deadline=time.monotonic() + 30
        )
        after_remove = await transport.procedure_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        isolated_after_remove = await transport.procedure_environment_status(
            isolated_lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if (
            removed.get("removed") is not True
            or isolated_removed.get("removed") is not True
            or after_remove.get("installed") is not False
            or isolated_after_remove.get("installed") is not False
        ):
            raise RuntimeError("Managed WSL environment removal did not close the exact fixture.")
        system_python_after = _system_python_snapshot(distribution)
        if system_python_after != system_python_before:
            raise RuntimeError("Procedure dependency install changed the WSL system Python.")
        return {
            "distribution": distribution,
            "environment_digest": lock.environment_digest(),
            "tree_sha256": installed["tree_sha256"],
            "installed_bytes": installed["installed_bytes"],
            "idempotent_install": True,
            "interrupted_install_recovered": True,
            "interrupted_install_unit_stopped": interrupted_unit_state == "inactive",
            "interrupted_staging_removed": interrupted_partial_state == "absent",
            "installer_service_sigkill_recovered": True,
            "integrity_status": True,
            "procedure_imports_from_read_only_mount": True,
            "other_environment_path_hidden": "other-environment-visible False" in isolation_output,
            "cross_environment_import_blocked": "cross-environment-import-blocked"
            in isolation_output,
            "active_use_removal_refused": active_removal_refused,
            "immutable_revision_identity": True,
            "rollback_to_previous_revision": True,
            "revision_removal_preserved_rollback_environment": True,
            "system_python_unchanged": True,
            "system_python_version": system_python_before["version"],
            "exact_environment_removed": True,
            "network": "none",
            "cleanup_confirmed": True,
        }


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(asyncio.run(run(args.distribution)), indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

"""Exercise the managed WSL installer with a local two-wheel lock fixture."""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import tempfile
import time
from pathlib import Path
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
from xaa.runtime.owned_transport import EnvironmentInUseError
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport


def build_wheel(name: str, value: str, requires_dist: str | None = None) -> tuple[str, bytes]:
    normalized = name.replace("-", "_")
    version = "1.0"
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


def make_lock(distribution: str, artifacts: dict[str, bytes]) -> ProcedurePythonDependencyLock:
    return ProcedurePythonDependencyLock(
        schema_version=1,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="a" * 64,
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
        requirements=("xaa-top==1.0",),
        wheels=tuple(
            LockedPythonWheel(
                filename=filename,
                project_name=filename.split("-", 1)[0].replace("_", "-"),
                version="1.0",
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                requires_dist=("xaa-leaf==1.0",) if filename.startswith("xaa_top-") else (),
            )
            for filename, content in sorted(artifacts.items())
        ),
    )


async def run(distribution: str) -> dict[str, Any]:
    leaf_name, leaf_bytes = build_wheel("xaa-leaf", "leaf-ready")
    top_name, top_bytes = build_wheel("xaa-top", "top-ready", "xaa-leaf==1.0")
    artifacts = {leaf_name: leaf_bytes, top_name: top_bytes}
    lock = make_lock(distribution, artifacts)
    with tempfile.TemporaryDirectory(prefix="xaa-procedure-environment-") as temporary:
        store = ProcedureWheelhouseStore(Path(temporary) / "wheelhouses")
        store.store(lock, artifacts)
        transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
        installed = await transport.install_procedure_environment(
            lock, store.root / lock.environment_digest(), deadline=time.monotonic() + 180
        )
        repeated = await transport.install_procedure_environment(
            lock, store.root / lock.environment_digest(), deadline=time.monotonic() + 60
        )
        observed = await transport.procedure_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if (
            installed.get("installed") is not True
            or installed.get("environment_digest") != lock.environment_digest()
            or installed.get("tree_sha256") != repeated.get("tree_sha256")
            or observed.get("tree_sha256") != installed.get("tree_sha256")
            or observed.get("lock_sha256")
            != hashlib.sha256(lock.canonical_json().encode()).hexdigest()
            or observed.get("installed_bytes", 0) <= 0
        ):
            raise RuntimeError("Managed WSL environment install or integrity status failed.")
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
            raise RuntimeError(
                "WSL did not execute the selected environment read-only: "
                f"receipt={execution!r}; stdout={output!r}; stderr={stderr!r}"
            )
        removed = await transport.remove_procedure_environment(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        after_remove = await transport.procedure_environment_status(
            lock.environment_digest(), deadline=time.monotonic() + 30
        )
        if removed.get("removed") is not True or after_remove.get("installed") is not False:
            raise RuntimeError("Managed WSL environment removal did not close the exact fixture.")
        return {
            "distribution": distribution,
            "environment_digest": lock.environment_digest(),
            "tree_sha256": installed["tree_sha256"],
            "installed_bytes": installed["installed_bytes"],
            "idempotent_install": True,
            "integrity_status": True,
            "procedure_imports_from_read_only_mount": True,
            "active_use_removal_refused": active_removal_refused,
            "exact_environment_removed": True,
            "network": "none",
            "cleanup_confirmed": True,
        }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    args = parser.parse_args()
    import json

    print(json.dumps(asyncio.run(run(args.distribution)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

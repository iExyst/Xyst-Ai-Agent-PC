"""Run untrusted npm manifest and lock parsing in a network-denied AppContainer."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from xaa.extensions.npm_dependencies import (
    MAX_LOCKFILE_BYTES,
    MAX_PACKAGE_MANIFEST_BYTES,
    ProcedureNpmDependencyLock,
)
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.windows_local_transport import WINDOWS_LOCAL_DISTRIBUTION, WindowsLocalTransport
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot

MAX_WORKER_REQUEST_BYTES = 64 * 1024
MAX_WORKER_RESULT_BYTES = 6 * 1024 * 1024
MAX_WORKER_SECONDS = 30


class NpmLockInspectionError(ValueError):
    """The isolated npm lock parser rejected its bounded input or result."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NpmLockInspectionError("The isolated npm parser returned duplicate fields.")
        result[key] = value
    return result


async def inspect_npm_package_lock_in_appcontainer(
    package_manifest: bytes,
    package_lock: bytes,
    *,
    skill_id: str,
    skill_version: int,
    source_sha256: str,
    distribution: str,
    node_version: str,
    npm_version: str,
    requirements: tuple[str, ...] | list[str],
    test_worker_executable: Path | None = None,
) -> ProcedureNpmDependencyLock:
    """Validate exact linked assets in a network-denied Windows AppContainer worker."""
    if os.name != "nt":
        raise NpmLockInspectionError("npm lock inspection requires Windows AppContainer isolation.")
    if not package_manifest or len(package_manifest) > MAX_PACKAGE_MANIFEST_BYTES:
        raise NpmLockInspectionError("Linked npm package.json exceeds its size limit.")
    if not package_lock or len(package_lock) > MAX_LOCKFILE_BYTES:
        raise NpmLockInspectionError("Linked npm package-lock.json exceeds its size limit.")
    if (
        not requirements
        or len(requirements) > 128
        or any(len(item) > 256 for item in requirements)
        or sum(len(item) for item in requirements) > 32 * 1024
    ):
        raise NpmLockInspectionError("Imported npm requirements exceed the supported limit.")
    try:
        transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
    except Exception as exc:
        raise NpmLockInspectionError(
            "Windows could not prepare isolated npm lock inspection."
        ) from exc

    manifest_sha256 = hashlib.sha256(package_manifest).hexdigest()
    execution_id = uuid4().hex
    with tempfile.TemporaryDirectory(prefix=f"xaa-npm-lock-owner-{execution_id}-") as owner_dir:
        if test_worker_executable is not None:
            try:
                executable = test_worker_executable.resolve(strict=True)
            except OSError as exc:
                raise NpmLockInspectionError("The frozen npm lock worker is unavailable.") from exc
            if executable.suffix.casefold() != ".exe" or not executable.is_file():
                raise NpmLockInspectionError("The frozen npm lock worker is invalid.")
            argv = [str(executable), "--npm-lock-worker"]
            source_worker = False
        elif getattr(sys, "frozen", False):
            argv = [sys.executable, "--npm-lock-worker"]
            source_worker = False
        else:
            engine_root = Path(__file__).resolve().parents[2]
            bootstrap = (
                "import sys; "
                f"sys.path.insert(0, {str(engine_root)!r}); "
                "from xaa.extensions.npm_lock_worker import main; "
                "raise SystemExit(main())"
            )
            argv = [sys.executable, "-s", "-c", bootstrap]
            source_worker = True
        intent = CommandIntent(
            execution_id=execution_id,
            backend="windows-appcontainer-v1",
            distribution=WINDOWS_LOCAL_DISTRIBUTION,
            profile_sha256=transport.profile_sha256,
            workspace=owner_dir,
            argv=argv,
            max_seconds=MAX_WORKER_SECONDS,
        )
        request = {
            "skill_id": skill_id,
            "skill_version": skill_version,
            "source_sha256": source_sha256,
            "package_manifest_sha256": manifest_sha256,
            "distribution": distribution,
            "node_version": node_version,
            "npm_version": npm_version,
            "requirements": list(requirements),
        }
        request_bytes = json.dumps(request, separators=(",", ":")).encode("utf-8")
        if len(request_bytes) > MAX_WORKER_REQUEST_BYTES:
            raise NpmLockInspectionError("npm lock inspection request exceeded its size limit.")
        snapshot = WorkspaceSnapshot(
            files=(
                SnapshotFile(path="request.json", content=request_bytes),
                SnapshotFile(path="package.json", content=package_manifest),
                SnapshotFile(path="package-lock.json", content=package_lock),
            ),
            directories=(),
        )
        try:
            receipt = await transport.run(
                intent,
                snapshot,
                deadline=time.monotonic() + MAX_WORKER_SECONDS + 10,
                isolated_python_worker=source_worker,
                max_seconds=MAX_WORKER_SECONDS,
                max_output_bytes=8 * 1024 * 1024,
            )
        except Exception as exc:
            raise NpmLockInspectionError("The isolated npm lock worker did not complete.") from exc

    if not receipt.get("succeeded") or not receipt.get("output_complete"):
        raise NpmLockInspectionError("The isolated npm lock worker rejected its input.")
    try:
        stdout_size = receipt.get("stdout_bytes")
        stdout_b64 = receipt.get("stdout_b64")
        if (
            type(stdout_size) is not int
            or not 1 <= stdout_size <= MAX_WORKER_RESULT_BYTES
            or not isinstance(stdout_b64, str)
            or len(stdout_b64) > 4 * ((MAX_WORKER_RESULT_BYTES + 2) // 3)
        ):
            raise ValueError("worker output size is invalid")
        output = base64.b64decode(stdout_b64, validate=True)
        if len(output) != stdout_size or not output.endswith(b"\n"):
            raise ValueError("worker output is malformed")
        result_value: object = json.loads(output[:-1], object_pairs_hook=_unique_object)
        if not isinstance(result_value, dict):
            raise TypeError("worker result is not an object")
        result = cast(dict[str, object], result_value)
        if set(result) != {"ok", "lock"} or result.get("ok") is not True:
            raise ValueError("worker result does not contain a lock")
        proposal = ProcedureNpmDependencyLock.model_validate(result["lock"], strict=True)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise NpmLockInspectionError(
            "The isolated npm lock worker returned an invalid result."
        ) from exc
    if (
        proposal.skill_id != skill_id
        or proposal.skill_version != skill_version
        or proposal.source_sha256 != source_sha256
        or proposal.package_manifest_sha256 != manifest_sha256
        or proposal.lockfile_sha256 != hashlib.sha256(package_lock).hexdigest()
        or proposal.distribution != distribution
        or proposal.node_version != node_version
        or proposal.npm_version != npm_version
        or proposal.requirements != tuple(requirements)
    ):
        raise NpmLockInspectionError("The isolated npm lock worker returned a mismatched identity.")
    return proposal

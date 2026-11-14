from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest
from xaa.extensions.npm_lock_inspector import (
    NpmLockInspectionError,
    inspect_npm_package_lock_in_appcontainer,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="npm lock parser AppContainer acceptance requires Windows."
)


def _fixtures() -> tuple[bytes, bytes]:
    package_manifest = b'{"name":"fixture","dependencies":{"left-pad":"^1.3.0"}}'
    archive_digest = base64.b64encode(hashlib.sha512(b"fixture tarball").digest()).decode("ascii")
    package_lock = json.dumps(
        {
            "name": "fixture",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "fixture", "dependencies": {"left-pad": "^1.3.0"}},
                "node_modules/left-pad": {
                    "version": "1.3.0",
                    "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                    "integrity": f"sha512-{archive_digest}",
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return package_manifest, package_lock


def _frozen_executable(frozen_worker: bool) -> Path | None:
    executable = (
        Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe").resolve()
        if frozen_worker
        else None
    )
    if frozen_worker and (executable is None or not executable.is_file()):
        pytest.skip("Build the frozen engine before running the packaged worker acceptance.")
    return executable


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen_worker", [False, True])
async def test_npm_lock_parser_isolated_from_the_api_process(frozen_worker: bool):
    package_manifest, package_lock = _fixtures()
    lock = await inspect_npm_package_lock_in_appcontainer(
        package_manifest,
        package_lock,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="a" * 64,
        distribution="Ubuntu-24.04",
        node_version="v22.2.0",
        npm_version="10.8.1",
        requirements=("left-pad@^1.3.0",),
        test_worker_executable=_frozen_executable(frozen_worker),
    )

    assert lock.packages[0].name == "left-pad"
    assert lock.packages[0].version == "1.3.0"
    assert lock.requirements == ("left-pad@^1.3.0",)
    assert lock.package_manifest_sha256 == hashlib.sha256(package_manifest).hexdigest()
    assert lock.lockfile_sha256 == hashlib.sha256(package_lock).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen_worker", [False, True])
async def test_npm_v1_lock_parser_isolated_from_the_api_process(frozen_worker: bool):
    package_manifest = b'{"name":"fixture","dependencies":{"left-pad":"^1.3.0"}}'
    archive_digest = base64.b64encode(hashlib.sha512(b"fixture tarball").digest()).decode("ascii")
    package_lock = json.dumps(
        {
            "name": "fixture",
            "lockfileVersion": 1,
            "dependencies": {
                "left-pad": {
                    "version": "1.3.0",
                    "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                    "integrity": f"sha512-{archive_digest}",
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    lock = await inspect_npm_package_lock_in_appcontainer(
        package_manifest,
        package_lock,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="a" * 64,
        distribution="Ubuntu-24.04",
        node_version="v22.2.0",
        npm_version="10.8.1",
        requirements=("left-pad@^1.3.0",),
        test_worker_executable=_frozen_executable(frozen_worker),
    )

    assert lock.packages[0].package_path == "node_modules/left-pad"
    assert lock.packages[0].version == "1.3.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen_worker", [False, True])
async def test_npm_lock_worker_rejects_manifest_lock_mismatch(frozen_worker: bool):
    _package_manifest, package_lock = _fixtures()
    mismatched_manifest = b'{"name":"fixture","dependencies":{"left-pad":"^2.0.0"}}'

    with pytest.raises(NpmLockInspectionError):
        await inspect_npm_package_lock_in_appcontainer(
            mismatched_manifest,
            package_lock,
            skill_id="12345678-1234-4234-8234-123456789abc",
            skill_version=1,
            source_sha256="a" * 64,
            distribution="Ubuntu-24.04",
            node_version="v22.2.0",
            npm_version="10.8.1",
            requirements=("left-pad@^1.3.0",),
            test_worker_executable=_frozen_executable(frozen_worker),
        )

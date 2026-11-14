from __future__ import annotations

import base64
import gzip
import hashlib
import io
import os
import tarfile
from pathlib import Path

import httpx
import pytest
from xaa.extensions.npm_dependencies import (
    NpmLockedPackage,
    NpmRootDependency,
    NpmVerifiedTarball,
    ProcedureNpmDependencyLock,
)
from xaa.extensions.npm_tarballs import NpmAcquisitionError, acquire_npm_tarballs_in_appcontainer

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="npm acquisition AppContainer acceptance requires Windows."
)


def _fixture(*, unsafe_member: str | None = None) -> tuple[ProcedureNpmDependencyLock, bytes]:
    metadata = b'{"name":"left-pad","version":"1.3.0","scripts":{"install":"node install.js"}}'
    content = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=content, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive,
    ):
        entries = [
            ("package/package.json", metadata),
            ("package/index.js", b"module.exports = function () {};\n"),
        ]
        if unsafe_member == "traversal":
            entries.append(("package/../escape.js", b"must-not-escape"))
        if unsafe_member == "duplicate":
            entries.append(("package/index.js", b"duplicate-member"))
        for name, value in entries:
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
        if unsafe_member == "symlink":
            member = tarfile.TarInfo("package/escape-link")
            member.type = tarfile.SYMTYPE
            member.linkname = "../escape.js"
            archive.addfile(member)
    tarball = content.getvalue()
    locked = NpmLockedPackage(
        package_path="node_modules/left-pad",
        name="left-pad",
        version="1.3.0",
        resolved_url="https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
        integrity="sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode("ascii"),
    )
    lock = ProcedureNpmDependencyLock(
        schema_version=1,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="a" * 64,
        package_manifest_sha256="b" * 64,
        lockfile_sha256="c" * 64,
        distribution="Ubuntu-24.04",
        node_version="v22.2.0",
        npm_version="10.8.1",
        requirements=(),
        root_dependencies=(
            NpmRootDependency(name="left-pad", version_range="^1.3.0", kind="dependencies"),
        ),
        packages=(locked,),
    )
    return lock, tarball


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen_worker", [False, True])
async def test_appcontainer_npm_acquisition_streams_only_locked_registry_tarballs(
    tmp_path: Path, frozen_worker: bool
):
    frozen_executable = (
        Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe").resolve()
        if frozen_worker
        else None
    )
    if frozen_worker and (frozen_executable is None or not frozen_executable.is_file()):
        pytest.skip("Build the frozen engine before running the packaged worker acceptance.")
    lock, tarball = _fixture()
    calls: list[str] = []

    async def fixture(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) != lock.packages[0].resolved_url:
            raise AssertionError(f"Unexpected registry request: {request.url}")
        return httpx.Response(
            200,
            headers={"content-length": str(len(tarball)), "content-type": "application/gzip"},
            content=tarball,
        )

    destination = tmp_path / "verified"
    destination.mkdir()
    reports: dict[str, NpmVerifiedTarball] = {}

    async def store_verified(
        paths: dict[str, Path], verified: dict[str, NpmVerifiedTarball]
    ) -> None:
        reports.update(verified)
        (destination / "package.tgz").write_bytes(paths[lock.packages[0].package_path].read_bytes())

    await acquire_npm_tarballs_in_appcontainer(
        lock,
        on_verified_files=store_verified,
        network_transport=httpx.MockTransport(fixture),
        test_worker_executable=frozen_executable,
    )

    assert calls == [lock.packages[0].resolved_url]
    assert (destination / "package.tgz").read_bytes() == tarball
    assert reports[lock.packages[0].package_path].install_lifecycle_scripts == ("install",)


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen_worker", [False, True])
async def test_appcontainer_npm_acquisition_rejects_registry_redirects(
    tmp_path: Path, frozen_worker: bool
):
    frozen_executable = (
        Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe").resolve()
        if frozen_worker
        else None
    )
    if frozen_worker and (frozen_executable is None or not frozen_executable.is_file()):
        pytest.skip("Build the frozen engine before running the packaged worker acceptance.")
    lock, _tarball = _fixture()
    calls: list[str] = []

    async def redirect(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://attacker.invalid/package.tgz"})

    async def discard(_paths: dict[str, Path], _verified: dict[str, NpmVerifiedTarball]) -> None:
        raise AssertionError("A redirected tarball must never be accepted.")

    with pytest.raises(NpmAcquisitionError):
        await acquire_npm_tarballs_in_appcontainer(
            lock,
            on_verified_files=discard,
            network_transport=httpx.MockTransport(redirect),
            test_worker_executable=frozen_executable,
        )
    assert calls == [lock.packages[0].resolved_url]


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen_worker", [False, True])
@pytest.mark.parametrize("unsafe_member", ["traversal", "symlink", "duplicate"])
async def test_appcontainer_npm_acquisition_rejects_hostile_tarball_members(
    tmp_path: Path, frozen_worker: bool, unsafe_member: str
):
    frozen_executable = (
        Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe").resolve()
        if frozen_worker
        else None
    )
    if frozen_worker and (frozen_executable is None or not frozen_executable.is_file()):
        pytest.skip("Build the frozen engine before running the packaged worker acceptance.")
    lock, tarball = _fixture(unsafe_member=unsafe_member)
    calls: list[str] = []

    async def fixture(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) != lock.packages[0].resolved_url:
            raise AssertionError(f"Unexpected registry request: {request.url}")
        return httpx.Response(
            200,
            headers={"content-length": str(len(tarball)), "content-type": "application/gzip"},
            content=tarball,
        )

    async def discard(_paths: dict[str, Path], _verified: dict[str, NpmVerifiedTarball]) -> None:
        raise AssertionError("A hostile tarball member must never be accepted.")

    with pytest.raises(NpmAcquisitionError):
        await acquire_npm_tarballs_in_appcontainer(
            lock,
            on_verified_files=discard,
            network_transport=httpx.MockTransport(fixture),
            test_worker_executable=frozen_executable,
        )
    assert calls == [lock.packages[0].resolved_url]

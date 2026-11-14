from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
from xaa.extensions.procedure_dependencies import PythonDependencyTarget
from xaa.extensions.pypi_resolver import resolve_in_appcontainer

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Resolver AppContainer acceptance requires Windows."
)


def _target() -> PythonDependencyTarget:
    return PythonDependencyTarget(
        distribution="Ubuntu-24.04",
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
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen_worker", [False, True])
async def test_appcontainer_resolver_uses_parent_pypi_broker_only(frozen_worker: bool):
    frozen_executable = (
        Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe").resolve()
        if frozen_worker
        else None
    )
    if frozen_worker and (frozen_executable is None or not frozen_executable.is_file()):
        pytest.skip("Build the frozen engine before running the packaged worker acceptance.")
    metadata = b"Metadata-Version: 2.3\nName: xaa-isolated\nVersion: 1.0\n\n"
    filename = "xaa_isolated-1.0-py3-none-any.whl"
    file_record = {
        "filename": filename,
        "url": f"https://files.pythonhosted.org/packages/fixture/{filename}",
        "hashes": {"sha256": "a" * 64},
        "size": 1024,
        "core-metadata": {"sha256": hashlib.sha256(metadata).hexdigest()},
        "requires-python": ">=3.8",
    }
    calls: list[str] = []

    async def fixture(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url == "https://pypi.org/simple/xaa-isolated/":
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.pypi.simple.v1+json"},
                content=json.dumps({"name": "xaa-isolated", "files": [file_record]}),
            )
        if request.url == f"https://files.pythonhosted.org/packages/fixture/{filename}.metadata":
            return httpx.Response(200, content=metadata)
        raise AssertionError(
            f"AppContainer worker requested an unapproved fixture URL: {request.url}"
        )

    lock = await resolve_in_appcontainer(
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="b" * 64,
        target=_target(),
        requirements=("xaa-isolated>=1", "xaa-isolated<2"),
        network_transport=httpx.MockTransport(fixture),
        test_worker_executable=frozen_executable,
    )

    assert lock.wheels[0].project_name == "xaa-isolated"
    assert lock.wheels[0].version == "1.0"
    assert calls == [
        "https://pypi.org/simple/xaa-isolated/",
        f"https://files.pythonhosted.org/packages/fixture/{filename}.metadata",
    ]

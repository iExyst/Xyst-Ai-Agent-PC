from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.docker_local_transport import DockerLocalTransport
from xaa.runtime.docker_resource_budget import (
    docker_budget_lock_path,
    docker_container_creation_lock,
)
from xaa.runtime.owned_transport import TransportError
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot


@pytest.mark.asyncio
async def test_docker_container_inventory_counts_all_xaa_profiled_containers() -> None:
    transport = DockerLocalTransport(
        "docker", "unix:///var/run/docker.sock", "python@sha256:" + "a" * 64, "b" * 64
    )
    expected = b"\n".join([b"c" * 64, b"d" * 64, b"e" * 64]) + b"\n"
    arguments: list[str] = []

    async def list_containers(command: tuple[str, ...], **_kwargs: object) -> bytes:
        arguments.extend(command)
        return expected

    transport._run_cli = list_containers  # type: ignore[method-assign]

    assert await transport.active_xaa_container_count(deadline=time.monotonic() + 5) == 3
    assert arguments == [
        "ps",
        "--all",
        "--no-trunc",
        "--quiet",
        "--filter",
        "label=com.xyst.xaa.profile",
    ]


@pytest.mark.asyncio
async def test_docker_container_inventory_rejects_malformed_ids() -> None:
    transport = DockerLocalTransport(
        "docker", "unix:///var/run/docker.sock", "python@sha256:" + "a" * 64, "b" * 64
    )

    async def list_containers(*_args: object, **_kwargs: object) -> bytes:
        return b"short-id\n"

    transport._run_cli = list_containers  # type: ignore[method-assign]

    with pytest.raises(TransportError, match="invalid X.A.A. container inventory"):
        await transport.active_xaa_container_count(deadline=time.monotonic() + 5)


@pytest.mark.asyncio
async def test_full_global_docker_budget_refuses_before_container_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    transport = DockerLocalTransport(
        "docker", "unix:///var/run/docker.sock", "python@sha256:" + "a" * 64, "b" * 64
    )
    command_intent = CommandIntent(
        argv=["python3", "-c", "print('no')"],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="b" * 64,
        image_reference="python@sha256:" + "a" * 64,
        workspace="budget-check",
    )
    ids = b"\n".join(bytes(char, "ascii") * 64 for char in "cdef") + b"\n"

    async def inspect(*_args: object, **_kwargs: object) -> None:
        return None

    async def list_containers(*_args: object, **_kwargs: object) -> bytes:
        return ids

    async def create_process(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Docker create must not run after global capacity is exhausted.")

    async def remove(*_args: object, **_kwargs: object) -> bool:
        return True

    transport._inspect_container = inspect  # type: ignore[method-assign]
    transport._run_cli = list_containers  # type: ignore[method-assign]
    transport._remove_container = remove  # type: ignore[method-assign]
    monkeypatch.setattr(
        "xaa.runtime.docker_local_transport.asyncio.create_subprocess_exec", create_process
    )

    with pytest.raises(TransportError, match="resource budget is full"):
        await transport.run(
            command_intent,
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + 30,
        )


def test_docker_creation_lock_path_is_shared_across_data_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_data = tmp_path / "user-data"
    monkeypatch.setenv("LOCALAPPDATA", str(user_data))
    monkeypatch.setenv("XAA_DATA_DIR", str(tmp_path / "profile-one"))
    first = docker_budget_lock_path()
    monkeypatch.setenv("XAA_DATA_DIR", str(tmp_path / "profile-two"))

    assert docker_budget_lock_path() == first == user_data / "XAA" / "docker-resource-budget.lock"


@pytest.mark.asyncio
async def test_docker_creation_lock_is_shared_between_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_data = tmp_path / "user-data"
    monkeypatch.setenv("LOCALAPPDATA", str(user_data))
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, environment.get("PYTHONPATH", "")) if value
    )

    # A separate interpreter proves the lock serializes independent engine processes.
    child_source = """import asyncio, sys, time
from xaa.runtime.docker_resource_budget import docker_container_creation_lock
async def main():
    try:
        async with docker_container_creation_lock(deadline=time.monotonic() + float(sys.argv[1])):
            print('acquired')
    except TimeoutError:
        print('timed_out')
asyncio.run(main())
"""

    async def launch(timeout: str) -> str:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            child_source,
            timeout,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        assert process.returncode == 0, stderr.decode(errors="replace")
        return stdout.decode().strip()

    async with docker_container_creation_lock(deadline=time.monotonic() + 5):
        assert await launch("0.2") == "timed_out"
    assert await launch("2") == "acquired"

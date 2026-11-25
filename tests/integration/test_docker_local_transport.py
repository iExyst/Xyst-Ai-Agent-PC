"""Opt-in acceptance tests against a real local Docker Engine and digest-pinned image."""

import asyncio
import base64
import os
import tempfile
import time
from uuid import uuid4

import pytest
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.docker_execution_policy import docker_create_argv
from xaa.runtime.docker_local_transport import DockerLocalTransport
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot

IMAGE_REFERENCE = os.environ.get("XAA_DOCKER_IMAGE_DIGEST", "")


def live_docker_required() -> None:
    if not IMAGE_REFERENCE:
        pytest.skip("Set XAA_DOCKER_IMAGE_DIGEST to a locally available repository@sha256 digest.")


@pytest.mark.asyncio
async def test_live_docker_runs_unicode_argv_and_discards_private_workspace_changes() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    await transport.verify_profile(deadline=time.monotonic() + 30)
    snapshot = WorkspaceSnapshot((SnapshotFile("input.txt", "fixture-λ".encode()),), (), ())
    intent = CommandIntent(
        argv=[
            "python3",
            "-I",
            "-c",
            "from pathlib import Path; print(Path('input.txt').read_text())",
        ],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        workspace="docker-live-acceptance",
    )
    result = await transport.run(intent, snapshot, deadline=time.monotonic() + 20)
    assert result["succeeded"] is True
    assert result["cleanup_confirmed"] is True
    assert result["workspace_changes_persisted"] is False
    assert result["snapshot_sha256"] == snapshot.sha256
    assert base64.b64decode(result["stdout_b64"], validate=True).decode("utf-8") == "fixture-λ\n"
    assert base64.b64decode(result["stderr_b64"], validate=True) == b""
    status = await transport.control("status", intent, deadline=time.monotonic() + 10)
    assert status["closed"] is True and status["manager_alive"] is False


@pytest.mark.asyncio
async def test_live_docker_timeout_removes_the_command_container() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    intent = CommandIntent(
        argv=["python3", "-I", "-c", "import time; time.sleep(60)"],
        max_seconds=2,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        workspace="docker-live-acceptance-timeout",
    )
    result = await transport.run(
        intent,
        WorkspaceSnapshot((), (), ()),
        deadline=time.monotonic() + 15,
    )
    assert result["termination_reason"] == "timeout"
    assert result["cleanup_confirmed"] is True
    status = await transport.control("status", intent, deadline=time.monotonic() + 10)
    assert status["closed"] is True and status["manager_alive"] is False


@pytest.mark.asyncio
async def test_live_docker_output_limit_returns_bounded_receipt_and_cleans_up() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    intent = CommandIntent(
        argv=["python3", "-I", "-c", "import sys; sys.stdout.write('x'*1100000)"],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        workspace="docker-live-acceptance-output",
    )
    result = await transport.run(
        intent,
        WorkspaceSnapshot((), (), ()),
        deadline=time.monotonic() + 20,
    )
    assert result["termination_reason"] == "output_limit"
    assert result["exit_code"] is None
    assert result["succeeded"] is False
    assert result["output_complete"] is False
    assert result["stdout_bytes"] + result["stderr_bytes"] <= 1024 * 1024
    assert result["cleanup_confirmed"] is True
    status = await transport.control("status", intent, deadline=time.monotonic() + 10)
    assert status["closed"] is True and status["manager_alive"] is False


@pytest.mark.asyncio
async def test_live_docker_enforces_the_process_limit() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    await transport.verify_profile(deadline=time.monotonic() + 30)
    code = """
import errno,os,signal,time
children=[]
try:
    for _ in range(40):
        try:
            pid=os.fork()
        except OSError as error:
            assert error.errno==errno.EAGAIN
            print(f'limit:{len(children)}',flush=True)
            break
        if pid==0:
            time.sleep(30)
            os._exit(0)
        children.append(pid)
    else:
        raise AssertionError('Docker did not enforce the process limit')
finally:
    for pid in children:
        try:
            os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
    for pid in children:
        try:
            os.waitpid(pid,0)
        except ChildProcessError:
            pass
"""
    intent = CommandIntent(
        argv=["python3", "-I", "-c", code],
        max_seconds=8,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        workspace="docker-live-acceptance-pids",
    )
    result = await transport.run(
        intent,
        WorkspaceSnapshot((), (), ()),
        deadline=time.monotonic() + 15,
    )
    assert result["succeeded"] is True
    assert result["cleanup_confirmed"] is True
    output = base64.b64decode(result["stdout_b64"], validate=True).decode("utf-8")
    assert output.startswith("limit:")
    count = int(output.removeprefix("limit:").strip())
    assert 1 <= count < 32


@pytest.mark.asyncio
async def test_live_docker_cpu_work_is_throttled_by_the_half_cpu_limit() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    code = """
import pathlib,time
paths=('/sys/fs/cgroup/cpu.stat','/sys/fs/cgroup/cpu/cpu.stat','/sys/fs/cgroup/cpu,cpuacct/cpu.stat')
def throttled_count():
    for path in paths:
        try:
            values=dict(line.split() for line in pathlib.Path(path).read_text().splitlines())
            return int(values['nr_throttled'])
        except OSError:
            continue
    raise AssertionError('Docker CPU throttle statistics are unavailable')
before=throttled_count()
deadline=time.monotonic()+3
value=0
while time.monotonic()<deadline:
    value=(value+1)%1000003
after=throttled_count()
assert after>before, (before,after,value)
print(f'throttled:{after-before}')
"""
    intent = CommandIntent(
        argv=["python3", "-I", "-c", code],
        max_seconds=7,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        workspace="docker-live-acceptance-cpu",
    )
    result = await transport.run(
        intent,
        WorkspaceSnapshot((), (), ()),
        deadline=time.monotonic() + 15,
    )
    assert result["succeeded"] is True
    assert result["cleanup_confirmed"] is True
    output = base64.b64decode(result["stdout_b64"], validate=True).decode("utf-8")
    assert output.startswith("throttled:")
    assert int(output.removeprefix("throttled:").strip()) > 0


@pytest.mark.asyncio
async def test_live_docker_stop_returns_cancelled_outcome_and_removes_container() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    intent = CommandIntent(
        argv=["python3", "-I", "-c", "import time; print('running',flush=True); time.sleep(60)"],
        max_seconds=20,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        workspace="docker-live-acceptance-cancel",
    )
    started = asyncio.Event()

    async def observe_output(_stream: str, chunk: bytes) -> None:
        if b"running" in chunk:
            started.set()

    run = asyncio.create_task(
        transport.run(
            intent,
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + 30,
            on_output=observe_output,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=15)
    await transport.control("stop", intent, deadline=time.monotonic() + 15)
    result = await asyncio.wait_for(run, timeout=20)
    assert result["termination_reason"] == "cancelled"
    assert result["exit_code"] is None
    assert result["output_complete"] is False
    assert result["cleanup_confirmed"] is True
    status = await transport.control("status", intent, deadline=time.monotonic() + 10)
    assert status["closed"] is True and status["manager_alive"] is False


@pytest.mark.asyncio
async def test_live_docker_recovery_removes_orphaned_container_after_manager_restart() -> None:
    """A fresh recovery-only transport removes a container left by a dead engine process."""
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
    )
    launcher = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    intent = CommandIntent(
        argv=["python3", "-I", "-c", "raise SystemExit('must never launch during recovery')"],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=launcher.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        workspace="docker-live-acceptance-recovery",
    )
    created = False
    try:

        async def docker_request(arguments: tuple[str, ...]) -> bytes:
            with tempfile.TemporaryDirectory(prefix="xaa-docker-recovery-test-") as config:
                environment = os.environ.copy()
                for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
                    environment.pop(key, None)
                environment["DOCKER_CONFIG"] = config
                process = await asyncio.create_subprocess_exec(
                    launcher.executable,
                    "--host",
                    launcher.endpoint,
                    *arguments,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
                assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
                return stdout

        create_argv = docker_create_argv(
            intent,
            IMAGE_REFERENCE,
            endpoint=launcher.endpoint,
            windows=os.name == "nt",
        )
        # This helper adds the endpoint itself; pass the subcommand once.
        raw_id = await docker_request(create_argv[3:])
        container_id = raw_id.decode("ascii", errors="strict").strip()
        assert len(container_id) == 64
        created = True
        await docker_request(("start", container_id))

        # Discovery from the saved intent is recovery-only and does not inspect or require
        # the image. This models a new engine process after the original manager disappeared.
        recovery = await DockerLocalTransport.discover(intent, deadline=time.monotonic() + 20)
        with pytest.raises(PermissionError, match="recovery-only"):
            recovery.require_intent(intent)
        status = await recovery.control("cleanup", intent, deadline=time.monotonic() + 20)
        assert status["closed"] is True
        assert status["cleanup_confirmed"] is True
        assert status["manager_alive"] is False
        assert status["outcome"] is None
        created = False
    finally:
        if created:
            recovery = await DockerLocalTransport.discover(intent, deadline=time.monotonic() + 20)
            await recovery.control("cleanup", intent, deadline=time.monotonic() + 20)

"""Opt-in acceptance tests against a real local Docker Engine and digest-pinned image."""

import asyncio
import base64
import io
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.command_controller import CommandController
from xaa.runtime.command_recovery import StoppedCommandRecovery
from xaa.runtime.docker_execution_policy import docker_create_argv
from xaa.runtime.docker_local_transport import DockerLocalTransport, _cli_request
from xaa.runtime.docker_session_archive import validate_session_archive
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.storage.commands import command_artifact_root
from xaa.storage.docker_sessions import DockerSessionJournal
from xaa.storage.runs import RunStore

IMAGE_REFERENCE = os.environ.get("XAA_DOCKER_IMAGE_DIGEST", "")


def live_docker_required() -> None:
    if not IMAGE_REFERENCE:
        pytest.skip("Set XAA_DOCKER_IMAGE_DIGEST to a locally available repository@sha256 digest.")


@pytest.mark.asyncio
async def test_live_docker_session_reuses_private_workspace_and_isolates_sessions() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        docker_session_reuse=True,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    first_session_id = uuid4().hex
    second_session_id = uuid4().hex
    first_intent = CommandIntent(
        argv=[
            "python3",
            "-I",
            "-c",
            "from pathlib import Path; Path('session-marker.txt').write_text('private-session-state')",
        ],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        docker_session_reuse=True,
        docker_session_id=first_session_id,
        workspace="docker-live-session-reuse",
    )
    second_intent = first_intent.model_copy(
        update={
            "argv": [
                "python3",
                "-I",
                "-c",
                "from pathlib import Path; print(Path('session-marker.txt').read_text())",
            ],
            "execution_id": uuid4().hex,
        }
    )
    isolated_intent = first_intent.model_copy(
        update={
            "argv": [
                "python3",
                "-I",
                "-c",
                "from pathlib import Path; print(Path('session-marker.txt').exists())",
            ],
            "execution_id": uuid4().hex,
            "docker_session_id": second_session_id,
        }
    )
    snapshot = WorkspaceSnapshot((), (), ())
    registered: list[str] = []

    async def register(container_id: str) -> None:
        registered.append(container_id)

    try:
        first = await transport.run(
            first_intent,
            snapshot,
            deadline=time.monotonic() + 30,
            on_session_container=register,
        )
        assert first["succeeded"] is True and first["docker_session_available"] is True
        second = await transport.run(
            second_intent,
            snapshot,
            deadline=time.monotonic() + 30,
        )
        assert second["succeeded"] is True and second["docker_session_available"] is True
        text = base64.b64decode(second["stdout_b64"], validate=True).decode().strip()
        assert text == "private-session-state"
        isolated = await transport.run(
            isolated_intent,
            snapshot,
            deadline=time.monotonic() + 30,
            on_session_container=register,
        )
        assert isolated["succeeded"] is True and isolated["docker_session_available"] is True
        isolated_text = base64.b64decode(isolated["stdout_b64"], validate=True).decode().strip()
        assert isolated_text == "False"
        assert len(registered) == 2 and all(len(value) == 64 for value in registered)
    finally:
        await transport.control("cleanup", first_intent, deadline=time.monotonic() + 15)
        await transport.control("cleanup", isolated_intent, deadline=time.monotonic() + 15)


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["timeout", "cancelled"])
async def test_live_docker_session_timeout_and_cancel_close_the_container(
    termination: str,
) -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        docker_session_reuse=True,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    intent = CommandIntent(
        argv=["python3", "-I", "-c", "import time; print('running',flush=True); time.sleep(60)"],
        max_seconds=2 if termination == "timeout" else 20,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        docker_session_reuse=True,
        docker_session_id=uuid4().hex,
        workspace="docker-live-session-termination",
    )
    started = asyncio.Event()

    async def register(_container_id: str) -> None:
        return None

    async def observe_output(_stream: str, chunk: bytes) -> None:
        if b"running" in chunk:
            started.set()

    run = asyncio.create_task(
        transport.run(
            intent,
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + 30,
            on_output=observe_output,
            on_session_container=register,
        )
    )
    try:
        if termination == "cancelled":
            await asyncio.wait_for(started.wait(), timeout=20)
            await transport.control("stop", intent, deadline=time.monotonic() + 15)
        result = await asyncio.wait_for(run, timeout=30)
        assert result["termination_reason"] == termination
        assert result["docker_session_available"] is False
        assert result["cleanup_confirmed"] is True
        status = await transport.control("status", intent, deadline=time.monotonic() + 10)
        assert status["closed"] is True and status["manager_alive"] is False
    finally:
        if not run.done():
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
        await transport.control("cleanup", intent, deadline=time.monotonic() + 15)


@pytest.mark.asyncio
async def test_live_docker_session_recovery_after_manager_restart_never_replays_command() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        docker_session_reuse=True,
    )
    launcher = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    intent = CommandIntent(
        argv=["python3", "-I", "-c", "print('original-command')"],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=launcher.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        docker_session_reuse=True,
        docker_session_id=uuid4().hex,
        workspace="docker-live-session-recovery",
    )
    registered: list[str] = []

    async def register(container_id: str) -> None:
        registered.append(container_id)

    try:
        result = await launcher.run(
            intent,
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + 30,
            on_session_container=register,
        )
        assert result["succeeded"] is True
        assert result["docker_session_available"] is True
        assert len(registered) == 1 and len(registered[0]) == 64

        # A fresh recovery-only transport stands in for the replacement engine
        # process. It can inspect and remove the saved session, never execute it.
        recovery = await DockerLocalTransport.discover(intent, deadline=time.monotonic() + 20)
        with pytest.raises(PermissionError, match="recovery-only"):
            recovery.require_intent(intent)
        status = await recovery.control("cleanup", intent, deadline=time.monotonic() + 20)
        assert status["closed"] is True
        assert status["cleanup_confirmed"] is True
        assert status["manager_alive"] is False
        assert status["outcome"] is None
        assert status["removed"] is True
    finally:
        await launcher.control("cleanup", intent, deadline=time.monotonic() + 15)


@pytest.mark.asyncio
async def test_live_docker_session_exports_bounded_archive_without_project_integration() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        docker_session_reuse=True,
        docker_session_export=True,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    intent = CommandIntent(
        argv=[
            "python3",
            "-I",
            "-c",
            "from pathlib import Path; Path('review.txt').write_text('owner review only')",
        ],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        docker_session_reuse=True,
        docker_session_export=True,
        docker_session_id=uuid4().hex,
        workspace="docker-live-session-export",
    )
    try:
        result = await transport.run(
            intent,
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + 30,
            on_session_container=lambda _container_id: asyncio.sleep(0),
        )
        assert result["succeeded"] is True and result["docker_session_available"] is True
        encoded = result["docker_session_archive_b64"]
        archive_data = base64.b64decode(encoded, validate=True)
        receipt = validate_session_archive(archive_data)
        assert receipt.size_bytes == result["docker_session_archive_bytes"]
        assert receipt.sha256 == result["docker_session_archive_sha256"]
        with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:") as archive:
            member = next(item for item in archive if item.name.endswith("review.txt"))
            extracted = archive.extractfile(member)
            assert extracted is not None and extracted.read() == b"owner review only"
    finally:
        await transport.control("cleanup", intent, deadline=time.monotonic() + 15)


@pytest.mark.asyncio
async def test_live_docker_session_controller_persists_archive_and_recovers_stopped_run(
    workspace_tmp_path: Path,
) -> None:
    live_docker_required()
    project = workspace_tmp_path / "project"
    project.mkdir()
    source = project / "source.txt"
    source.write_text("original project", encoding="utf-8")
    profile_request = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        max_seconds=30,
        docker_session_reuse=True,
        docker_session_export=True,
    )
    transport = await DockerLocalTransport.discover(profile_request, deadline=time.monotonic() + 20)
    grant = profile_request.model_copy(update={"profile_sha256": transport.profile_sha256})
    data = workspace_tmp_path / "data"
    data.mkdir()
    store = RunStore(data / "coordinator.sqlite3")
    run = store.create_run(
        objective="Verify reviewed Docker session artifacts",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant=grant.model_dump(mode="json"),
    )
    owner = store.acquire_lease(run["id"], "live-docker-coordinator", seconds=120)
    assert owner is not None
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    controller = CommandController(store, transport)
    sessions = DockerSessionJournal(store)

    async def same_live_transport(_intent: CommandIntent) -> DockerLocalTransport:
        return transport

    second_run: dict[str, object] | None = None
    second_owner: str | None = None
    try:
        first = await controller.execute(
            run["id"],
            owner,
            "create-session-file",
            {
                "argv": [
                    "python3",
                    "-c",
                    "from pathlib import Path; Path('/workspace/session.txt').write_text('owner review', encoding='utf-8')",
                ]
            },
            idempotency_key="create-session-file",
            deadline=time.monotonic() + 45,
        )
        assert first["succeeded"] is True
        assert first["session_archive_artifact_id"]
        with store.connection() as connection:
            row = connection.execute(
                "SELECT id FROM docker_sessions WHERE run_id=?", (run["id"],)
            ).fetchone()
        assert row is not None
        session_id = str(row["id"])
        session = sessions.get(session_id)
        assert session is not None and session.state == "ready"

        second = await controller.execute(
            run["id"],
            owner,
            "read-session-file",
            {
                "argv": [
                    "python3",
                    "-c",
                    "from pathlib import Path; print(Path('/workspace/session.txt').read_text(encoding='utf-8'))",
                ]
            },
            idempotency_key="read-session-file",
            deadline=time.monotonic() + 45,
        )
        assert second["succeeded"] is True
        assert second["session_archive_artifact_id"]
        assert source.read_text(encoding="utf-8") == "original project"

        artifact_record = store.get_artifact(second["session_archive_artifact_id"])
        assert artifact_record is not None
        archive_path = command_artifact_root(store) / artifact_record["storage_key"]
        archive_bytes = archive_path.read_bytes()
        receipt = validate_session_archive(archive_bytes)
        assert receipt.sha256 == artifact_record["sha256"]
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            member = next(item for item in archive if item.name.rsplit("/", 1)[-1] == "session.txt")
            extracted = archive.extractfile(member)
            assert extracted is not None and extracted.read() == b"owner review"

        second_run = store.create_run(
            objective="Verify cross-run Docker session isolation",
            acceptance_criteria=[],
            workspace=str(project),
            mode="workspace",
            model=None,
            command_grant=grant.model_dump(mode="json"),
        )
        second_owner = store.acquire_lease(
            str(second_run["id"]), "live-docker-second-run", seconds=120
        )
        assert second_owner is not None
        store.transition(str(second_run["id"]), RunState.PLANNING, owner=second_owner)
        store.transition(str(second_run["id"]), RunState.RUNNING, owner=second_owner)
        isolated = await controller.execute(
            str(second_run["id"]),
            second_owner,
            "verify-cross-run-isolation",
            {
                "argv": [
                    "python3",
                    "-c",
                    "from pathlib import Path; raise SystemExit(9 if Path('/workspace/session.txt').exists() else 0)",
                ]
            },
            idempotency_key="verify-cross-run-isolation",
            deadline=time.monotonic() + 45,
        )
        assert isolated["succeeded"] is True
        assert isolated["session_archive_artifact_id"]
        with store.connection() as connection:
            row = connection.execute(
                "SELECT id FROM docker_sessions WHERE run_id=?", (second_run["id"],)
            ).fetchone()
        assert row is not None and str(row["id"]) != session_id
        isolated_artifact = store.get_artifact(isolated["session_archive_artifact_id"])
        assert isolated_artifact is not None
        isolated_archive = command_artifact_root(store) / isolated_artifact["storage_key"]
        with tarfile.open(fileobj=io.BytesIO(isolated_archive.read_bytes()), mode="r:") as archive:
            assert all(item.name.rsplit("/", 1)[-1] != "session.txt" for item in archive)
        assert source.read_text(encoding="utf-8") == "original project"

        store.transition(str(second_run["id"]), RunState.FAILED, owner=second_owner)
        assert await StoppedCommandRecovery(store, same_live_transport).run_once() == 1
        isolated_session = sessions.get(str(row["id"]))
        assert isolated_session is not None and isolated_session.state == "closed"

        store.transition(run["id"], RunState.FAILED, owner=owner)
        assert await StoppedCommandRecovery(store, same_live_transport).run_once() == 1
        session = sessions.get(session_id)
        assert session is not None and session.state == "closed"
    finally:
        if second_run is not None and second_owner is not None:
            current_second = store.get_run(str(second_run["id"]))
            if current_second is not None and current_second["state"] == "running":
                store.transition(str(second_run["id"]), RunState.FAILED, owner=second_owner)
            if current_second is not None and current_second["state"] in {
                "running",
                "failed",
                "cancelled",
            }:
                await StoppedCommandRecovery(store, same_live_transport).run_once()
        current = store.get_run(run["id"])
        if current is not None and current["state"] == "running":
            store.transition(run["id"], RunState.FAILED, owner=owner)
        if current is not None and current["state"] in {"running", "failed", "cancelled"}:
            await StoppedCommandRecovery(store, same_live_transport).run_once()


@pytest.mark.asyncio
async def test_live_docker_bridge_access_requires_the_explicit_grant() -> None:
    live_docker_required()
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        docker_network_access=True,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 20)
    code, stdout, _stderr = await _cli_request(
        transport.executable,
        transport.endpoint,
        ("network", "inspect", "bridge", "--format", "{{json .IPAM.Config}}"),
        deadline=time.monotonic() + 10,
    )
    assert code == 0
    configs = json.loads(stdout)
    assert isinstance(configs, list) and configs and isinstance(configs[0], dict)
    gateway = configs[0].get("Gateway")
    assert isinstance(gateway, str) and gateway

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.read(32)
            writer.write(b"bridge-grant:" + request)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(respond, "0.0.0.0", 0)
    port = server.sockets[0].getsockname()[1]
    intent = CommandIntent(
        argv=[
            "python3",
            "-I",
            "-c",
            f"import socket; s=socket.create_connection(({gateway!r},{port}),timeout=5); s.sendall(b'xaa'); print(s.recv(64).decode()); s.close()",
        ],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        docker_network_access=True,
        workspace="docker-live-bridge-network",
    )
    try:
        result = await transport.run(
            intent,
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + 20,
        )
    finally:
        server.close()
        await server.wait_closed()
    assert result["succeeded"] is True
    assert result["cleanup_confirmed"] is True
    assert (
        base64.b64decode(result["stdout_b64"], validate=True).decode().strip() == "bridge-grant:xaa"
    )


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

"""Opt-in acceptance tests against a real local Docker Engine and digest-pinned image."""

import asyncio
import base64
import io
import json
import os
import shlex
import signal
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.command_controller import CommandController
from xaa.runtime.command_recovery import StoppedCommandRecovery
from xaa.runtime.docker_execution_policy import (
    MAX_REUSABLE_DOCKER_SESSIONS,
    docker_create_argv,
)
from xaa.runtime.docker_local_transport import DockerLocalTransport, _cli_request
from xaa.runtime.docker_session_archive import validate_session_archive
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_worker import execute_filesystem
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.storage.commands import CommandJournal, command_artifact_root
from xaa.storage.docker_sessions import DockerSessionJournal
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.tasks import TaskStore

IMAGE_REFERENCE = os.environ.get("XAA_DOCKER_IMAGE_DIGEST", "")


def live_docker_required() -> None:
    if not IMAGE_REFERENCE:
        pytest.skip("Set XAA_DOCKER_IMAGE_DIGEST to a locally available repository@sha256 digest.")


@pytest.mark.asyncio
async def test_live_docker_profile_session_budget_caps_aggregate_container_resources(
    workspace_tmp_path: Path,
) -> None:
    live_docker_required()
    project = workspace_tmp_path / "project"
    project.mkdir()
    request = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        max_seconds=10,
        docker_session_reuse=True,
    )
    transport = await DockerLocalTransport.discover(request, deadline=time.monotonic() + 20)
    grant = request.model_copy(update={"profile_sha256": transport.profile_sha256})
    data = workspace_tmp_path / "data"
    data.mkdir()
    store = RunStore(data / "session-budget.sqlite3")
    owned_runs: list[tuple[str, str]] = []
    for index in range(MAX_REUSABLE_DOCKER_SESSIONS + 1):
        run = store.create_run(
            objective="Verify bounded aggregate Docker session resources",
            acceptance_criteria=[],
            workspace=str(project),
            mode="workspace",
            model=None,
            command_grant=grant.model_dump(mode="json"),
        )
        run_id = str(run["id"])
        owner = store.acquire_lease(run_id, f"docker-session-budget-{index}", seconds=120)
        assert owner is not None
        store.transition(run_id, RunState.PLANNING, owner=owner)
        store.transition(run_id, RunState.RUNNING, owner=owner)
        owned_runs.append((run_id, owner))

    controller = CommandController(store, transport)
    sessions = DockerSessionJournal(store)

    async def same_live_transport(_intent: CommandIntent) -> DockerLocalTransport:
        return transport

    try:
        for index, (run_id, owner) in enumerate(owned_runs[:MAX_REUSABLE_DOCKER_SESSIONS]):
            outcome = await controller.execute(
                run_id,
                owner,
                f"create-budget-session-{index}",
                {"argv": ["python3", "-I", "-c", f"print('session-{index}')"]},
                idempotency_key=f"create-budget-session-{index}",
                deadline=time.monotonic() + 30,
            )
            assert outcome["succeeded"] is True
        with store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM docker_sessions WHERE state!='closed' ORDER BY created_at"
            ).fetchall()
        assert len(rows) == MAX_REUSABLE_DOCKER_SESSIONS
        assert all(row["state"] == "ready" and row["container_id"] for row in rows)

        overflow_run_id, overflow_owner = owned_runs[MAX_REUSABLE_DOCKER_SESSIONS]
        with pytest.raises(RuntimeError, match="resource budget is full"):
            await controller.execute(
                overflow_run_id,
                overflow_owner,
                "deny-over-budget-session",
                {"argv": ["python3", "-I", "-c", "print('must-not-run')"]},
                idempotency_key="deny-over-budget-session",
                deadline=time.monotonic() + 30,
            )
        assert len(sessions.terminal()) == 0
    finally:
        for run_id, owner in owned_runs:
            run = store.get_run(run_id)
            if run is not None and run["state"] in {"queued", "planning", "running", "verifying"}:
                store.transition(run_id, RunState.FAILED, owner=owner)
        await StoppedCommandRecovery(store, same_live_transport).run_once()
    with store.connection() as connection:
        open_sessions = connection.execute(
            "SELECT COUNT(*) FROM docker_sessions WHERE state!='closed'"
        ).fetchone()[0]
    assert open_sessions == 0


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


@pytest.mark.parametrize(
    "hold_output",
    [False, True],
    ids=["active-command", "output-before-result-receipt"],
)
@pytest.mark.asyncio
async def test_live_docker_session_recovery_after_worker_process_death_never_replays(
    workspace_tmp_path: Path,
    hold_output: bool,
) -> None:
    live_docker_required()
    if os.name != "posix":
        pytest.skip("Process-group death acceptance requires the hosted POSIX Docker runner.")

    project = workspace_tmp_path / "project"
    project.mkdir()
    request = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        max_seconds=30,
        docker_session_reuse=True,
    )
    transport = await DockerLocalTransport.discover(request, deadline=time.monotonic() + 20)
    grant = request.model_copy(update={"profile_sha256": transport.profile_sha256})
    data = workspace_tmp_path / "data"
    data.mkdir()
    store = RunStore(data / "crash-recovery.sqlite3")
    run = store.create_run(
        objective="Recover an interrupted Docker session without replay",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant=grant.model_dump(mode="json"),
    )
    owner = store.acquire_lease(run["id"], "docker-worker-crash", seconds=120)
    assert owner is not None
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    call_id = "docker-crash-active-call"
    command_source = (
        "from pathlib import Path; Path('/workspace/started').write_text('yes'); "
        "print('docker-crash-marker', flush=True)"
    )
    if not hold_output:
        command_source += "; import time; time.sleep(300)"
    intent = CommandJournal(store).reserve(
        run["id"],
        owner,
        call_id,
        {
            "argv": [
                "python3",
                "-I",
                "-c",
                command_source,
            ],
            "max_seconds": 30,
        },
        idempotency_key=call_id,
    )
    assert intent.docker_session_id is not None
    sessions = DockerSessionJournal(store)
    session = sessions.get(intent.docker_session_id)
    assert session is not None and session.state == "reserved"
    sessions.transition(session.id, owner, expected={"reserved"}, destination="creating")

    registration = workspace_tmp_path / "container-id.txt"
    registered_ack = workspace_tmp_path / "container-registered"
    command_started = workspace_tmp_path / "command-started"
    output_waiting = workspace_tmp_path / "output-waiting"
    release_output = workspace_tmp_path / "release-output"
    worker_code = """
import asyncio, json, pathlib, sys, time
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.docker_local_transport import DockerLocalTransport
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot

async def main():
    values = json.loads(sys.argv[1])
    intent = CommandIntent.model_validate(values["intent"])
    grant = CommandGrant(
        backend="docker-local-v1", distribution="Docker-local",
        profile_sha256="0" * 64, image_reference=intent.image_reference,
        max_seconds=30, docker_session_reuse=True,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 30)
    async def register(container_id):
        pathlib.Path(values["registration"]).write_text(container_id, encoding="ascii")
        deadline = time.monotonic() + 30
        while not pathlib.Path(values["registered_ack"]).exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Session journal registration was not acknowledged.")
            await asyncio.sleep(0.02)
    async def output(_stream, chunk):
        if b"docker-crash-marker" in chunk:
            pathlib.Path(values["command_started"]).write_text("started", encoding="ascii")
            if values["hold_output"]:
                pathlib.Path(values["output_waiting"]).write_text("waiting", encoding="ascii")
                deadline = time.monotonic() + 30
                while not pathlib.Path(values["release_output"]).exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("The output receipt was not released.")
                    await asyncio.sleep(0.02)
    await transport.run(
        intent, WorkspaceSnapshot((), (), ()), deadline=time.monotonic() + 90,
        on_session_container=register, on_output=output,
    )

asyncio.run(main())
"""
    worker: asyncio.subprocess.Process | None = None

    async def recover(_intent: CommandIntent) -> DockerLocalTransport:
        return await DockerLocalTransport.discover(_intent, deadline=time.monotonic() + 20)

    recovery = StoppedCommandRecovery(store, recover)
    try:
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            worker_code,
            json.dumps(
                {
                    "intent": intent.model_dump(mode="json"),
                    "registration": str(registration),
                    "registered_ack": str(registered_ack),
                    "command_started": str(command_started),
                    "hold_output": hold_output,
                    "output_waiting": str(output_waiting),
                    "release_output": str(release_output),
                }
            ),
            cwd=str(Path.cwd()),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        deadline = time.monotonic() + 60
        while not registration.exists():
            if worker.returncode is not None:
                stderr = await worker.stderr.read() if worker.stderr is not None else b""
                raise AssertionError(f"Docker worker exited before session creation: {stderr!r}")
            if time.monotonic() >= deadline:
                raise TimeoutError("Docker worker did not register its session container.")
            await asyncio.sleep(0.05)
        container_id = registration.read_text(encoding="ascii")
        sessions.transition(
            session.id,
            owner,
            expected={"creating"},
            destination="ready",
            container_id=container_id,
        )
        sessions.transition(
            session.id,
            owner,
            expected={"ready"},
            destination="busy",
            execution_id=intent.execution_id,
        )
        registered_ack.write_text("saved", encoding="ascii")
        deadline = time.monotonic() + 45
        while not command_started.exists():
            if worker.returncode is not None:
                stderr = await worker.stderr.read() if worker.stderr is not None else b""
                raise AssertionError(f"Docker worker exited before active command: {stderr!r}")
            if time.monotonic() >= deadline:
                raise TimeoutError("Docker command did not reach its active marker.")
            await asyncio.sleep(0.05)
        if hold_output:
            deadline = time.monotonic() + 10
            while not output_waiting.exists():
                if worker.returncode is not None:
                    stderr = await worker.stderr.read() if worker.stderr is not None else b""
                    raise AssertionError(f"Docker output callback exited early: {stderr!r}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Docker output callback did not hold result receipt.")
                await asyncio.sleep(0.02)

        status = await transport.control("status", intent, deadline=time.monotonic() + 10)
        assert status["manager_alive"] is True and status["closed"] is False
        os.killpg(worker.pid, signal.SIGKILL)
        await asyncio.wait_for(worker.wait(), timeout=15)
        assert worker.returncode == -signal.SIGKILL
        interrupted = sessions.get(session.id)
        assert interrupted is not None and interrupted.state == "busy"
        assert interrupted.active_execution_id == intent.execution_id

        stopped = store.transition(run["id"], RunState.FAILED, owner=owner)
        assert stopped["lease_owner"] is None
        assert await recovery.run_once() == 2
        closed = sessions.get(session.id)
        assert closed is not None and closed.state == "closed"
        tool_call = store.unresolved_tool_calls(run["id"])
        assert tool_call == []
        with store.connection() as connection:
            call = connection.execute(
                "SELECT state,result FROM tool_calls WHERE run_id=? AND provider_call_id=?",
                (run["id"], call_id),
            ).fetchone()
            completions = connection.execute(
                "SELECT COUNT(*) FROM run_events WHERE run_id=? AND event_type='execution.completed'",
                (run["id"],),
            ).fetchone()[0]
        assert call is not None and call["state"] == "failed"
        assert json.loads(call["result"])["verification"] == "not_verified"
        assert completions == 0
    finally:
        if worker is not None and worker.returncode is None:
            os.killpg(worker.pid, signal.SIGKILL)
            await asyncio.gather(worker.wait(), return_exceptions=True)
        current = store.get_run(run["id"])
        if current is not None and current["state"] == "running":
            store.transition(run["id"], RunState.FAILED, owner=owner)
        current = store.get_run(run["id"])
        if current is not None and current["state"] in {"failed", "cancelled"}:
            recovery.retry_after.clear()
            await recovery.run_once()


@pytest.mark.asyncio
async def test_live_docker_session_cannot_read_host_sentinel_file(
    workspace_tmp_path: Path,
) -> None:
    live_docker_required()
    sentinel = workspace_tmp_path / "docker-host-private-sentinel.txt"
    sentinel.write_text("host-only-xaa-sentinel", encoding="utf-8")
    request = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        max_seconds=15,
        docker_session_reuse=True,
    )
    transport = await DockerLocalTransport.discover(request, deadline=time.monotonic() + 20)
    intent = CommandIntent(
        argv=[
            "python3",
            "-I",
            "-c",
            f"from pathlib import Path; sentinel = Path({str(sentinel)!r}); print(sentinel.read_text(encoding='utf-8') if sentinel.is_file() else 'not-visible')",
        ],
        max_seconds=15,
        execution_id=uuid4().hex,
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256=transport.profile_sha256,
        image_reference=IMAGE_REFERENCE,
        docker_session_reuse=True,
        docker_session_id=uuid4().hex,
        workspace="docker-live-host-sentinel",
    )

    async def register(_container_id: str) -> None:
        return None

    try:
        result = await transport.run(
            intent,
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + 30,
            on_session_container=register,
        )
        assert result["succeeded"] is True
        assert base64.b64decode(result["stdout_b64"], validate=True).decode().strip() == (
            "not-visible"
        )
        assert sentinel.read_text(encoding="utf-8") == "host-only-xaa-sentinel"
        assert result["cleanup_confirmed"] is True
    finally:
        await transport.control("cleanup", intent, deadline=time.monotonic() + 15)


@pytest.mark.asyncio
async def test_live_docker_session_recovery_after_container_create_before_registration(
    workspace_tmp_path: Path,
) -> None:
    live_docker_required()
    if os.name != "posix":
        pytest.skip("Process-group death acceptance requires the hosted POSIX Docker runner.")

    project = workspace_tmp_path / "project"
    project.mkdir()
    request = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        max_seconds=30,
        docker_session_reuse=True,
    )
    transport = await DockerLocalTransport.discover(request, deadline=time.monotonic() + 20)
    grant = request.model_copy(update={"profile_sha256": transport.profile_sha256})
    data = workspace_tmp_path / "data"
    data.mkdir()
    store = RunStore(data / "create-crash-recovery.sqlite3")
    run = store.create_run(
        objective="Recover an unregistered Docker container after worker death",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant=grant.model_dump(mode="json"),
    )
    owner = store.acquire_lease(run["id"], "docker-create-worker-crash", seconds=120)
    assert owner is not None
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    call_id = "docker-create-crash-call"
    intent = CommandJournal(store).reserve(
        run["id"],
        owner,
        call_id,
        {"argv": ["python3", "-I", "-c", "print('must-not-run')"], "max_seconds": 20},
        idempotency_key=call_id,
    )
    assert intent.docker_session_id is not None
    sessions = DockerSessionJournal(store)
    session = sessions.get(intent.docker_session_id)
    assert session is not None and session.state == "reserved"
    sessions.transition(session.id, owner, expected={"reserved"}, destination="creating")

    creation_finished = workspace_tmp_path / "docker-create-finished"
    registration = workspace_tmp_path / "docker-container-registered"
    wrapper = workspace_tmp_path / "docker-wrapper"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'{shlex.quote(transport.executable)} "$@"\n'
        "status=$?\n"
        'if [ "$status" -eq 0 ] && [ "$1" = "--host" ] && [ "$3" = "create" ]; then\n'
        f"  printf 'created\\n' > {shlex.quote(str(creation_finished))}\n"
        "  sleep 300\n"
        "fi\n"
        'exit "$status"\n',
        encoding="utf-8",
    )
    os.chmod(wrapper, 0o755)
    worker_code = """
import asyncio, json, pathlib, sys, time
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.docker_local_transport import DockerLocalTransport
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot

async def main():
    values = json.loads(sys.argv[1])
    intent = CommandIntent.model_validate(values["intent"])
    grant = CommandGrant(
        backend="docker-local-v1", distribution="Docker-local",
        profile_sha256="0" * 64, image_reference=intent.image_reference,
        max_seconds=30, docker_session_reuse=True,
    )
    transport = await DockerLocalTransport.discover(grant, deadline=time.monotonic() + 30)
    transport.executable = values["wrapper"]
    async def register(container_id):
        pathlib.Path(values["registration"]).write_text(container_id, encoding="ascii")
    await transport.run(
        intent, WorkspaceSnapshot((), (), ()), deadline=time.monotonic() + 90,
        on_session_container=register,
    )

asyncio.run(main())
"""
    worker: asyncio.subprocess.Process | None = None

    async def recover(_intent: CommandIntent) -> DockerLocalTransport:
        return await DockerLocalTransport.discover(_intent, deadline=time.monotonic() + 20)

    recovery = StoppedCommandRecovery(store, recover)
    try:
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            worker_code,
            json.dumps(
                {
                    "intent": intent.model_dump(mode="json"),
                    "wrapper": str(wrapper),
                    "registration": str(registration),
                }
            ),
            cwd=str(Path.cwd()),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        deadline = time.monotonic() + 60
        while not creation_finished.exists():
            if worker.returncode is not None:
                stderr = await worker.stderr.read() if worker.stderr is not None else b""
                raise AssertionError(f"Docker worker exited before container creation: {stderr!r}")
            if time.monotonic() >= deadline:
                raise TimeoutError("Docker create command did not reach its crash boundary.")
            await asyncio.sleep(0.02)
        container = await transport._inspect_container(intent, deadline=time.monotonic() + 10)
        assert container is not None
        state_value = container.get("State")
        assert isinstance(state_value, dict) and state_value.get("Status") == "created"
        assert not registration.exists()
        creating = sessions.get(session.id)
        assert creating is not None and creating.state == "creating"
        assert creating.container_id is None

        os.killpg(worker.pid, signal.SIGKILL)
        await asyncio.wait_for(worker.wait(), timeout=15)
        assert worker.returncode == -signal.SIGKILL
        stopped = store.transition(run["id"], RunState.FAILED, owner=owner)
        assert stopped["lease_owner"] is None
        assert await recovery.run_once() == 2
        closed = sessions.get(session.id)
        assert closed is not None and closed.state == "closed"
        assert await transport._inspect_container(intent, deadline=time.monotonic() + 10) is None
        assert store.unresolved_tool_calls(run["id"]) == []
        with store.connection() as connection:
            call = connection.execute(
                "SELECT state,result FROM tool_calls WHERE run_id=? AND provider_call_id=?",
                (run["id"], call_id),
            ).fetchone()
            completions = connection.execute(
                "SELECT COUNT(*) FROM run_events WHERE run_id=? AND event_type='execution.completed'",
                (run["id"],),
            ).fetchone()[0]
        assert call is not None and call["state"] == "failed"
        assert json.loads(call["result"])["verification"] == "not_verified"
        assert completions == 0
    finally:
        if worker is not None and worker.returncode is None:
            os.killpg(worker.pid, signal.SIGKILL)
            await asyncio.gather(worker.wait(), return_exceptions=True)
        current = store.get_run(run["id"])
        if current is not None and current["state"] == "running":
            store.transition(run["id"], RunState.FAILED, owner=owner)
        current = store.get_run(run["id"])
        if current is not None and current["state"] in {"failed", "cancelled"}:
            recovery.retry_after.clear()
            await recovery.run_once()


@pytest.mark.asyncio
async def test_live_docker_session_recovery_after_worker_death_during_close(
    workspace_tmp_path: Path,
) -> None:
    live_docker_required()
    if os.name != "posix":
        pytest.skip("Process-group death acceptance requires the hosted POSIX Docker runner.")

    project = workspace_tmp_path / "project"
    project.mkdir()
    request = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE_REFERENCE,
        max_seconds=20,
        docker_session_reuse=True,
    )
    transport = await DockerLocalTransport.discover(request, deadline=time.monotonic() + 20)
    grant = request.model_copy(update={"profile_sha256": transport.profile_sha256})
    data = workspace_tmp_path / "data"
    data.mkdir()
    store = RunStore(data / "close-crash-recovery.sqlite3")
    run = store.create_run(
        objective="Recover a Docker session interrupted during close",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant=grant.model_dump(mode="json"),
    )
    owner = store.acquire_lease(run["id"], "docker-close-worker-crash", seconds=120)
    assert owner is not None
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    controller = CommandController(store, transport)
    result = await controller.execute(
        run["id"],
        owner,
        "docker-close-seed-call",
        {"argv": ["python3", "-I", "-c", "print('close-seed-finished')"]},
        idempotency_key="docker-close-seed-call",
        deadline=time.monotonic() + 45,
    )
    assert result["succeeded"] is True
    with store.connection() as connection:
        session_id = connection.execute(
            "SELECT id FROM docker_sessions WHERE run_id=?", (run["id"],)
        ).fetchone()["id"]
    sessions = DockerSessionJournal(store)
    ready = sessions.get(session_id)
    assert ready is not None and ready.state == "ready"
    intent = sessions.saved_intent(session_id)
    failed = store.transition(run["id"], RunState.FAILED, owner=owner)
    assert failed["lease_owner"] is None
    closing = sessions.get(session_id)
    assert closing is not None and closing.state == "stopping"

    rm_finished = workspace_tmp_path / "docker-rm-finished"
    wrapper = workspace_tmp_path / "docker-close-wrapper"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'{shlex.quote(transport.executable)} "$@"\n'
        "status=$?\n"
        'if [ "$status" -eq 0 ] && [ "$1" = "--host" ] && [ "$3" = "rm" ]; then\n'
        f"  printf 'removed\\n' > {shlex.quote(str(rm_finished))}\n"
        "  sleep 300\n"
        "fi\n"
        'exit "$status"\n',
        encoding="utf-8",
    )
    os.chmod(wrapper, 0o755)
    worker_code = """
import asyncio, pathlib, sys, time
from xaa.runtime.command_recovery import StoppedCommandRecovery
from xaa.runtime.docker_local_transport import DockerLocalTransport
from xaa.storage.runs import RunStore

async def main():
    store = RunStore(pathlib.Path(sys.argv[1]))
    async def discover(intent):
        transport = await DockerLocalTransport.discover(intent, deadline=time.monotonic() + 20)
        transport.executable = sys.argv[2]
        return transport
    await StoppedCommandRecovery(store, discover).run_once()

asyncio.run(main())
"""
    worker: asyncio.subprocess.Process | None = None

    async def recover(_intent: CommandIntent) -> DockerLocalTransport:
        return await DockerLocalTransport.discover(_intent, deadline=time.monotonic() + 20)

    recovery = StoppedCommandRecovery(store, recover)
    try:
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            worker_code,
            str(data / "close-crash-recovery.sqlite3"),
            str(wrapper),
            cwd=str(Path.cwd()),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        deadline = time.monotonic() + 45
        while not rm_finished.exists():
            if worker.returncode is not None:
                stderr = await worker.stderr.read() if worker.stderr is not None else b""
                raise AssertionError(f"Docker close worker exited before removal: {stderr!r}")
            if time.monotonic() >= deadline:
                raise TimeoutError("Docker session cleanup did not reach its removal boundary.")
            await asyncio.sleep(0.02)
        assert await transport._inspect_container(intent, deadline=time.monotonic() + 10) is None
        os.killpg(worker.pid, signal.SIGKILL)
        await asyncio.wait_for(worker.wait(), timeout=15)
        assert worker.returncode == -signal.SIGKILL
        interrupted = sessions.get(session_id)
        assert interrupted is not None and interrupted.state == "stopping"

        assert await recovery.run_once() == 1
        closed = sessions.get(session_id)
        assert closed is not None and closed.state == "closed"
        assert await transport._inspect_container(intent, deadline=time.monotonic() + 10) is None
        assert store.unresolved_tool_calls(run["id"]) == []
    finally:
        if worker is not None and worker.returncode is None:
            os.killpg(worker.pid, signal.SIGKILL)
            await asyncio.gather(worker.wait(), return_exceptions=True)
        current = store.get_run(run["id"])
        if current is not None and current["state"] == "running":
            store.transition(run["id"], RunState.FAILED, owner=owner)
        current = store.get_run(run["id"])
        if current is not None and current["state"] in {"failed", "cancelled"}:
            recovery.retry_after.clear()
            await recovery.run_once()


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

        task_store = TaskStore(store)
        task_store.save_plan(
            run["id"],
            owner,
            [
                TaskDefinition(
                    id=task_id,
                    objective="Verify private task Docker session isolation",
                    acceptance_criteria=["Only this task session sees its private file"],
                    permissions=["workspace.read", "workspace.write", "workspace.command"],
                )
                for task_id in ("docker-task-a", "docker-task-b")
            ],
        )
        task_scopes: dict[str, TaskReadScope] = {}
        for _ in range(2):
            claim = task_store.claim_ready(run["id"], owner)
            assert claim is not None
            task_id = str(claim["task"]["local_id"])
            task_scopes[task_id] = TaskReadScope(run["id"], owner, task_id, str(claim["token"]))
            await execute_filesystem(
                store,
                run["id"],
                owner,
                f"{task_id}-branch-init",
                "branch_init",
                {},
                idempotency_key=f"{task_id}-branch-init",
                internal=True,
                task_id=task_id,
                task_token=task_scopes[task_id].task_token,
            )
        task_write = await CommandController(
            store, transport, task_scope=task_scopes["docker-task-a"]
        ).execute(
            run["id"],
            owner,
            "write-task-private-file",
            {
                "argv": [
                    "python3",
                    "-c",
                    "from pathlib import Path; Path('/workspace/task-only.txt').write_text('private task A')",
                ]
            },
            idempotency_key="write-task-private-file",
            deadline=time.monotonic() + 45,
        )
        assert task_write["succeeded"] is True
        task_isolated = await CommandController(
            store, transport, task_scope=task_scopes["docker-task-b"]
        ).execute(
            run["id"],
            owner,
            "verify-task-private-session",
            {
                "argv": [
                    "python3",
                    "-c",
                    "from pathlib import Path; raise SystemExit(9 if Path('/workspace/task-only.txt').exists() else 0)",
                ]
            },
            idempotency_key="verify-task-private-session",
            deadline=time.monotonic() + 45,
        )
        assert task_isolated["succeeded"] is True
        assert source.read_text(encoding="utf-8") == "original project"

        store.transition(run["id"], RunState.FAILED, owner=owner)
        assert await StoppedCommandRecovery(store, same_live_transport).run_once() == 3
        session = sessions.get(session_id)
        assert session is not None and session.state == "closed"
        with store.connection() as connection:
            rows = connection.execute(
                "SELECT task_scope,id,state FROM docker_sessions WHERE run_id=?",
                (run["id"],),
            ).fetchall()
        assert {str(item["task_scope"]) for item in rows} == {
            "parent",
            f"{run['id']}:docker-task-a",
            f"{run['id']}:docker-task-b",
        }
        assert all(str(item["state"]) == "closed" for item in rows)
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

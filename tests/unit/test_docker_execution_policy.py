import asyncio
import base64
import io
import json
import os
import re
import tarfile
import time

import pytest
from xaa.runtime.command_contracts import CommandGrant, CommandIntent, command_tool_description
from xaa.runtime.docker_execution_policy import (
    docker_container_name,
    docker_copy_argv,
    docker_create_argv,
    docker_exec_argv,
    validate_image_reference,
    validate_local_engine_endpoint,
)
from xaa.runtime.docker_local_probe import PROBE_CODE
from xaa.runtime.docker_local_transport import (
    DockerLocalTransport,
    _require_container_policy,
    _snapshot_tar,
)
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.storage.commands import CommandOutcome

IMAGE = "registry.example/xaa/python@sha256:" + "a" * 64
WINDOWS_ENDPOINT = "npipe:////./pipe/docker_engine"
LINUX_ENDPOINT = "unix:///var/run/docker.sock"


def test_docker_readiness_probe_checks_effective_cgroup_limits() -> None:
    compile(PROBE_CODE, "docker-readiness-probe.py", "exec")
    for limit in (
        "memory.max",
        "memory.swap.max",
        "cpu.max",
        "pids.max",
        "memory.memsw.limit_in_bytes",
        "cpu.cfs_quota_us",
    ):
        assert limit in PROBE_CODE
    assert "{'cpu','memory','pids'} <= controllers" in PROBE_CODE
    assert "/proc/self/mountinfo" in PROBE_CODE
    assert "mount['fstype']=='tmpfs'" in PROBE_CODE


def intent(**overrides: object) -> CommandIntent:
    values: dict[str, object] = {
        "execution_id": "b" * 32,
        "argv": ["python", "-c", "print('hello')"],
        "max_seconds": 10,
        "backend": "docker-local-v1",
        "distribution": "Docker-local",
        "profile_sha256": "c" * 64,
        "image_reference": IMAGE,
        "workspace": "fixture",
    }
    values.update(overrides)
    return CommandIntent.model_validate(values)


@pytest.mark.parametrize(
    "reference", ["python:3.12", "python@sha256:" + "A" * 64, "", "sha256:" + "a" * 64]
)
def test_docker_requires_repository_and_lowercase_immutable_digest(reference: str) -> None:
    with pytest.raises(ValueError, match="pinned by SHA-256"):
        validate_image_reference(reference)


def test_docker_grant_persists_exact_immutable_image_and_explains_scope() -> None:
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="c" * 64,
        image_reference=IMAGE,
    )
    assert grant.model_dump(mode="json")["image_reference"] == IMAGE
    description = command_tool_description(grant)
    assert IMAGE in description and "Network is denied" in description
    network_grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="c" * 64,
        image_reference=IMAGE,
        docker_network_access=True,
    )
    assert "bridge-network access is owner-approved" in command_tool_description(network_grant)
    assert network_grant.model_dump(mode="json")["docker_network_access"] is True
    with pytest.raises(ValueError, match="pinned image"):
        CommandGrant(
            backend="docker-local-v1",
            distribution="Docker-local",
            profile_sha256="c" * 64,
        )
    with pytest.raises(ValueError, match="only for the Docker backend"):
        CommandGrant(
            backend="wsl-bubblewrap-v1",
            distribution="Ubuntu-24.04",
            profile_sha256="c" * 64,
            image_reference=IMAGE,
        )
    with pytest.raises(ValueError, match="only for the Docker backend"):
        CommandGrant(
            backend="wsl-bubblewrap-v1",
            distribution="Ubuntu-24.04",
            profile_sha256="c" * 64,
            docker_network_access=True,
        )


def test_docker_network_mode_is_bound_to_the_saved_intent() -> None:
    command = docker_create_argv(
        intent(docker_network_access=True), IMAGE, endpoint=LINUX_ENDPOINT, windows=False
    )
    assert command[command.index("--network") + 1] == "bridge"
    default_command = docker_create_argv(intent(), IMAGE, endpoint=LINUX_ENDPOINT, windows=False)
    assert default_command[default_command.index("--network") + 1] == "none"
    with pytest.raises(ValueError, match="only for the Docker backend"):
        CommandIntent(
            execution_id="f" * 32,
            argv=["python", "--version"],
            backend="wsl-bubblewrap-v1",
            distribution="Ubuntu-24.04",
            profile_sha256="c" * 64,
            docker_network_access=True,
            workspace="fixture",
        )


def test_docker_intent_cannot_change_the_granted_image() -> None:
    transport = DockerLocalTransport("docker", LINUX_ENDPOINT, IMAGE, "c" * 64)
    with pytest.raises(PermissionError, match="saved backend grant"):
        docker_create_argv(
            intent(image_reference="registry.example/xaa/python@sha256:" + "d" * 64),
            IMAGE,
            endpoint=LINUX_ENDPOINT,
            windows=False,
        )
    with pytest.raises(PermissionError, match="does not match the Docker image profile"):
        transport.require_intent(intent(docker_network_access=True))
    network_transport = DockerLocalTransport(
        "docker", LINUX_ENDPOINT, IMAGE, "c" * 64, docker_network_access=True
    )
    network_transport.require_intent(intent(docker_network_access=True))


def test_only_explicit_local_daemon_endpoints_are_allowed() -> None:
    assert validate_local_engine_endpoint(WINDOWS_ENDPOINT, windows=True) == WINDOWS_ENDPOINT
    assert validate_local_engine_endpoint(LINUX_ENDPOINT, windows=False) == LINUX_ENDPOINT
    for endpoint, windows in (
        ("tcp://127.0.0.1:2375", True),
        ("ssh://builder@example", True),
        (LINUX_ENDPOINT, True),
        (WINDOWS_ENDPOINT, False),
    ):
        with pytest.raises(PermissionError, match="local engine endpoint"):
            validate_local_engine_endpoint(endpoint, windows=windows)


@pytest.mark.parametrize(
    ("endpoint", "windows"),
    [(WINDOWS_ENDPOINT, True), (LINUX_ENDPOINT, False)],
)
def test_docker_create_has_fixed_isolation_and_bounded_private_workspace(
    endpoint: str, windows: bool
) -> None:
    command = docker_create_argv(intent(), IMAGE, endpoint=endpoint, windows=windows)
    assert command[:3] == ("docker", "--host", endpoint)
    assert "create" in command
    assert "--pull=never" in command
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--ipc" in command and command[command.index("--ipc") + 1] == "private"
    assert "--shm-size" in command and command[command.index("--shm-size") + 1] == "33554432"
    assert "--read-only" in command
    assert "--cap-drop" in command and command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges=true" in command
    assert "--pids-limit" in command and command[command.index("--pids-limit") + 1] == "32"
    assert "--memory" in command and command[command.index("--memory") + 1] == "134217728"
    assert "--memory-swap" in command and command[command.index("--memory-swap") + 1] == "134217728"
    assert "--cpus" in command and command[command.index("--cpus") + 1] == "0.5"
    assert "--user" in command and command[command.index("--user") + 1] == "65534:65534"
    assert command.count("--tmpfs") == 2
    assert "/workspace:rw,noexec,nosuid,nodev,size=33554432,mode=1777" in command
    assert "--mount" not in command and "--volume" not in command
    assert command[-3:] == (IMAGE, "-c", "while :; do sleep 3600; done")
    assert not any("docker.sock" in value or "docker_engine" in value for value in command[3:])


def test_docker_exec_uses_saved_identity_and_structured_argv() -> None:
    command = docker_exec_argv(intent(), endpoint=LINUX_ENDPOINT, windows=False)
    assert command[:3] == ("docker", "--host", LINUX_ENDPOINT)
    assert command[-4:] == (
        docker_container_name("b" * 32),
        "python",
        "-c",
        "print('hello')",
    )


def test_docker_snapshot_copy_uses_tar_over_stdin_without_host_mounts() -> None:
    command = docker_copy_argv(intent(), endpoint=LINUX_ENDPOINT, windows=False)
    assert command[-5:] == ("tar", "-xf", "-", "-C", "/workspace")
    assert "--mount" not in command and "--volume" not in command


def test_docker_snapshot_tar_contains_only_validated_regular_files_and_directories() -> None:
    snapshot = WorkspaceSnapshot(
        (SnapshotFile("src/main.py", b"print('fixture')"),),
        ("src",),
    )
    with tarfile.open(fileobj=io.BytesIO(_snapshot_tar(snapshot))) as archive:
        members = archive.getmembers()
        assert [(member.name, member.isdir(), member.isfile()) for member in members] == [
            ("src", True, False),
            ("src/main.py", False, True),
        ]
        source = archive.extractfile("src/main.py")
        assert source is not None and source.read() == b"print('fixture')"


def test_docker_container_inspect_must_confirm_requested_isolation() -> None:
    container: dict[str, object] = {
        "Config": {"User": "65534:65534"},
        "HostConfig": {
            "NetworkMode": "none",
            "IpcMode": "private",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges=true"],
            "PidsLimit": 32,
            "Memory": 134217728,
            "MemorySwap": 134217728,
            "ShmSize": 33554432,
            "NanoCpus": 500000000,
            "Tmpfs": {
                "/tmp": "rw,noexec,nosuid,nodev,size=33554432",
                "/workspace": "rw,noexec,nosuid,nodev,size=33554432,mode=1777",
            },
        },
        "State": {"Running": True},
        "Mounts": [
            {"Type": "tmpfs", "Destination": "/tmp"},
            {"Type": "tmpfs", "Destination": "/workspace"},
            {"Type": "tmpfs", "Destination": "/dev/shm"},
        ],
    }
    _require_container_policy(container)

    # Docker Engine may omit tmpfs entries from Mounts; HostConfig plus the
    # actual in-container fixed probe establishes the effective scratch mounts.
    inspect_without_tmpfs_entries = json.loads(json.dumps(container))
    inspect_without_tmpfs_entries["Mounts"] = []
    _require_container_policy(inspect_without_tmpfs_entries)

    unsafe = json.loads(json.dumps(container))
    unsafe["HostConfig"]["NetworkMode"] = "bridge"
    with pytest.raises(PermissionError, match="isolation limits"):
        _require_container_policy(unsafe)
    _require_container_policy(unsafe, network_access=True)

    unsafe["HostConfig"]["NetworkMode"] = "host"
    with pytest.raises(PermissionError, match="isolation limits"):
        _require_container_policy(unsafe, network_access=True)

    unsafe = json.loads(json.dumps(container))
    unsafe["Mounts"][0] = {"Type": "bind", "Destination": "/tmp"}
    with pytest.raises(PermissionError, match="unexpected host or volume mount"):
        _require_container_policy(unsafe)

    unsafe = json.loads(json.dumps(container))
    unsafe["HostConfig"]["Tmpfs"]["/workspace"] = "rw,size=33554432"
    with pytest.raises(PermissionError, match="bounded workspace mounts"):
        _require_container_policy(unsafe)

    unsafe = json.loads(json.dumps(container))
    unsafe["HostConfig"]["ShmSize"] = 67108864
    with pytest.raises(PermissionError, match="isolation limits"):
        _require_container_policy(unsafe)


@pytest.mark.asyncio
async def test_docker_image_inspection_requires_exact_digest_and_no_implicit_volumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = json.dumps(
        {"Id": "sha256:" + "b" * 64, "RepoDigests": [IMAGE], "Config": {"Volumes": None}}
    ).encode()

    async def inspect(*_args: object, **_kwargs: object) -> tuple[int, bytes, bytes]:
        return 0, response, b""

    monkeypatch.setattr("xaa.runtime.docker_local_transport._cli_request", inspect)
    image = await DockerLocalTransport._inspect_image(
        "docker", LINUX_ENDPOINT, IMAGE, deadline=10**12
    )
    assert image["Id"] == "sha256:" + "b" * 64

    for invalid in (
        {"Id": "sha256:" + "b" * 64, "RepoDigests": ["python:latest"], "Config": {"Volumes": None}},
        {"Id": "sha256:" + "b" * 64, "RepoDigests": [IMAGE], "Config": {"Volumes": {"/host": {}}}},
    ):

        async def invalid_inspect(
            *_args: object, value: dict[str, object] = invalid, **_kwargs: object
        ):
            return 0, json.dumps(value).encode(), b""

        monkeypatch.setattr("xaa.runtime.docker_local_transport._cli_request", invalid_inspect)
        with pytest.raises(OSError, match="exact Docker image digest"):
            await DockerLocalTransport._inspect_image(
                "docker", LINUX_ENDPOINT, IMAGE, deadline=10**12
            )


@pytest.mark.asyncio
async def test_docker_readiness_probe_checks_nonce_result_and_container_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = DockerLocalTransport("docker", LINUX_ENDPOINT, IMAGE, "c" * 64)
    actions: list[str] = []

    async def run(command_intent, snapshot, *, deadline):
        assert command_intent.argv == ["python3", "-I", "/workspace/probe.py"]
        nonce = snapshot.files[1].content.decode("ascii")
        output = json.dumps(
            {"nonce": nonce, "checks": True, "network_denied": True}, separators=(",", ":")
        ).encode()
        return {
            "execution_id": command_intent.execution_id,
            "succeeded": True,
            "exit_code": 0,
            "supervisor_exit_code": 0,
            "termination_reason": "finished",
            "cleanup_confirmed": True,
            "snapshot_sha256": snapshot.sha256,
            "filter_sha256": transport.profile_sha256,
            "output_complete": True,
            "workspace_changes_persisted": False,
            "stdout_b64": base64.b64encode(output).decode("ascii"),
            "stdout_bytes": len(output),
        }

    async def control(action, _intent, *, deadline):
        actions.append(action)
        return {
            "cleanup_confirmed": True,
            "closed": action == "status",
            "manager_alive": False,
        }

    monkeypatch.setattr(transport, "run", run)
    monkeypatch.setattr(transport, "control", control)
    await transport.verify_profile(deadline=10**12)
    assert actions == ["cleanup", "status"]


@pytest.mark.asyncio
async def test_docker_profile_changes_when_daemon_security_environment_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = {
        "ID": "daemon-id",
        "ServerVersion": "27.0.0",
        "OSType": "linux",
        "KernelVersion": "6.8.0",
        "SecurityOptions": ["name=seccomp", "name=cgroupns"],
    }

    async def inspect_info(*_args: object, **_kwargs: object) -> dict[str, object]:
        return info

    async def inspect_image(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"Id": "sha256:" + "c" * 64}

    monkeypatch.setattr("xaa.runtime.docker_local_transport.os.name", "nt")
    monkeypatch.setattr("xaa.runtime.docker_local_transport.shutil.which", lambda _name: "docker")
    monkeypatch.setattr(DockerLocalTransport, "_inspect_info", inspect_info)
    monkeypatch.setattr(DockerLocalTransport, "_inspect_image", inspect_image)
    grant = CommandGrant(
        backend="docker-local-v1",
        distribution="Docker-local",
        profile_sha256="0" * 64,
        image_reference=IMAGE,
    )
    initial = await DockerLocalTransport.discover(grant, deadline=10**12)
    info["KernelVersion"] = "6.9.0"
    changed = await DockerLocalTransport.discover(grant, deadline=10**12)
    assert initial.profile_sha256 != changed.profile_sha256


@pytest.mark.asyncio
async def test_docker_output_overflow_returns_a_bounded_output_limit_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = WINDOWS_ENDPOINT if os.name == "nt" else LINUX_ENDPOINT
    transport = DockerLocalTransport("docker", endpoint, IMAGE, "c" * 64)
    container_id = "d" * 64

    class FakeProcess:
        def __init__(self, *, create: bool) -> None:
            self.returncode: int | None = None
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self._create = create
            if not create:
                self.stdout.feed_data(b"x" * (1024 * 1024 + 16384))
                self.stdout.feed_eof()
                self.stderr.feed_eof()

        async def communicate(self, _payload: bytes | None = None) -> tuple[bytes, bytes]:
            self.returncode = 0
            return container_id.encode("ascii"), b""

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    spawned: list[FakeProcess] = []

    async def spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        process = FakeProcess(create=not spawned)
        spawned.append(process)
        return process

    async def run_cli(arguments: tuple[str, ...], **_kwargs: object) -> bytes:
        code = arguments[-1] if arguments else ""
        match = re.search(r"write_text\('([0-9a-f]{32})'", code)
        if match is not None:
            return json.dumps(
                {"nonce": match.group(1), "checks": True, "network_denied": True}
            ).encode()
        return b""

    async def inspect(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "Config": {"User": "65534:65534"},
            "HostConfig": {
                "NetworkMode": "none",
                "IpcMode": "private",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges=true"],
                "PidsLimit": 32,
                "Memory": 134217728,
                "MemorySwap": 134217728,
                "ShmSize": 33554432,
                "NanoCpus": 500000000,
                "Tmpfs": {
                    "/tmp": "rw,noexec,nosuid,nodev,size=33554432",
                    "/workspace": "rw,noexec,nosuid,nodev,size=33554432,mode=1777",
                },
            },
            "State": {"Running": True, "OOMKilled": False},
            "Mounts": [
                {"Type": "tmpfs", "Destination": "/tmp"},
                {"Type": "tmpfs", "Destination": "/workspace"},
                {"Type": "tmpfs", "Destination": "/dev/shm"},
            ],
        }

    async def remove(*_args: object, **_kwargs: object) -> bool:
        return True

    async def stop(process: FakeProcess) -> None:
        process.returncode = 137

    monkeypatch.setattr("xaa.runtime.docker_local_transport.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr(transport, "_run_cli", run_cli)
    monkeypatch.setattr(transport, "_inspect_container", inspect)
    monkeypatch.setattr(transport, "_remove_container", remove)
    monkeypatch.setattr("xaa.runtime.docker_local_transport.stop_owned_process", stop)
    result = await transport.run(
        intent(), WorkspaceSnapshot((), (), ()), deadline=time.monotonic() + 60
    )
    assert result["termination_reason"] == "output_limit"
    assert result["exit_code"] is None
    assert result["output_complete"] is False
    assert result["succeeded"] is False
    assert result["stdout_bytes"] + result["stderr_bytes"] <= 1024 * 1024
    assert result["cleanup_confirmed"] is True
    outcome = CommandOutcome.model_validate(
        {
            **{key: result[key] for key in CommandOutcome.model_fields if key in result},
            "stdout_artifact_id": "stdout-artifact",
            "stderr_artifact_id": "stderr-artifact",
        }
    )
    assert outcome.termination_reason == "output_limit" and outcome.exit_code is None


@pytest.mark.asyncio
async def test_docker_stop_marks_an_active_command_for_cancelled_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = WINDOWS_ENDPOINT if os.name == "nt" else LINUX_ENDPOINT
    transport = DockerLocalTransport("docker", endpoint, IMAGE, "c" * 64)
    command_intent = intent()
    transport._executing.add(command_intent.execution_id)

    async def inspect(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"State": {"Running": False}}

    monkeypatch.setattr(transport, "_inspect_container", inspect)
    result = await transport.control("stop", command_intent, deadline=time.monotonic() + 10)
    assert command_intent.execution_id in transport._cancelled
    assert result["manager_alive"] is False

"""Local Docker Engine transport using only bounded private container storage."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from xaa.runtime.cleanup import finish_cleanup
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.command_transport import OutputCallback
from xaa.runtime.docker_execution_policy import (
    docker_container_name,
    docker_copy_argv,
    docker_create_argv,
    docker_exec_argv,
    validate_image_reference,
    validate_local_engine_endpoint,
)
from xaa.runtime.docker_local_probe import PROBE_CODE
from xaa.runtime.owned_transport import TransportError
from xaa.runtime.processes import stop_owned_process
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot, checked_path

MAX_OUTPUT_BYTES = 1024 * 1024
MAX_CLI_RESPONSE_BYTES = 256 * 1024
POLICY_VERSION = "xaa-docker-local-v2"
EXECUTION_LABEL = "com.xyst.xaa.execution"


class _DockerOutputLimitExceeded(TransportError):
    """The bounded capture stopped after Docker emitted too much output."""


def _endpoint() -> str:
    if os.name == "nt":
        return "npipe:////./pipe/docker_engine"
    if os.name == "posix":
        return "unix:///var/run/docker.sock"
    raise OSError("Docker command execution is unsupported on this operating system.")


def _safe_environment(executable: str, config: str) -> dict[str, str]:
    parent = str(Path(executable).resolve().parent)
    environment = {
        "PATH": os.pathsep.join((parent, os.environ.get("PATH", ""))),
        "DOCKER_CONFIG": config,
        "HOME": config,
        "TMPDIR": config,
        "TEMP": config,
        "TMP": config,
    }
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        environment.update(
            {
                "SystemRoot": system_root,
                "WINDIR": system_root,
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
            }
        )
    return environment


async def _cli_request(
    executable: str,
    endpoint: str,
    arguments: tuple[str, ...],
    *,
    payload: bytes = b"",
    deadline: float,
    output_limit: int = MAX_CLI_RESPONSE_BYTES,
    allow_failure: bool = False,
) -> tuple[int, bytes, bytes]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Docker request exceeded its execution deadline.")
    command = (executable, "--host", endpoint, *arguments)
    with tempfile.TemporaryDirectory(prefix="xaa-docker-cli-") as config:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_safe_environment(executable, config),
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
        except OSError as exc:
            raise OSError("Could not start the local Docker CLI.") from exc
        try:
            async with asyncio.timeout(remaining):
                stdout, stderr = await process.communicate(payload)
            if len(stdout) + len(stderr) > output_limit:
                raise TransportError("Docker CLI exceeded its bounded response size.")
            if process.returncode is None:
                raise TransportError("Docker CLI exited without a result code.")
            if process.returncode != 0 and not allow_failure:
                raise TransportError(
                    f"Local Docker request failed with exit code {process.returncode}."
                )
            return process.returncode, stdout, stderr
        finally:
            if process.returncode is None:
                await stop_owned_process(process)


def _snapshot_tar(snapshot: WorkspaceSnapshot) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for directory in sorted(
            snapshot.directories,
            key=lambda value: (len(checked_path(value).parts), value),
        ):
            name = checked_path(directory).as_posix()
            info = tarfile.TarInfo(name + "/")
            info.type = tarfile.DIRTYPE
            info.mode = 0o700
            info.mtime = 0
            archive.addfile(info)
        for entry in sorted(snapshot.files, key=lambda value: value.path):
            name = checked_path(entry.path).as_posix()
            info = tarfile.TarInfo(name)
            info.type = tarfile.REGTYPE
            info.mode = 0o600
            info.size = len(entry.content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(entry.content))
    value = buffer.getvalue()
    if len(value) > 12 * 1024 * 1024:
        raise ValueError("Docker workspace snapshot exceeds the transfer limit.")
    return value


def _require_container_policy(container: dict[str, Any], *, network_access: bool = False) -> None:
    """Fail closed unless the daemon reports the limits we requested."""
    config_value = container.get("Config")
    host_value = container.get("HostConfig")
    state_value = container.get("State")
    mounts_value = container.get("Mounts")
    if not all(isinstance(value, dict) for value in (config_value, host_value, state_value)):
        raise TransportError("Docker returned incomplete container policy metadata.")
    if not isinstance(mounts_value, list):
        raise TransportError("Docker returned incomplete container mount metadata.")
    mounts = cast(list[Any], mounts_value)
    config = cast(dict[str, Any], config_value)
    host = cast(dict[str, Any], host_value)
    state = cast(dict[str, Any], state_value)
    tmpfs_value = host.get("Tmpfs")
    if not isinstance(tmpfs_value, dict):
        raise PermissionError("Docker did not apply the private bounded workspace mounts.")
    tmpfs = cast(dict[str, Any], tmpfs_value)

    def mount_options(value: object) -> set[str]:
        if not isinstance(value, str):
            return set()
        return {option.strip() for option in value.split(",") if option.strip()}

    expected_tmpfs = {
        "/tmp": {"rw", "noexec", "nosuid", "nodev", "size=33554432"},
        "/workspace": {"rw", "noexec", "nosuid", "nodev", "size=33554432", "mode=1777"},
    }
    if {path: mount_options(options) for path, options in tmpfs.items()} != expected_tmpfs:
        raise PermissionError("Docker did not apply the private bounded workspace mounts.")
    expected_host = {
        "NetworkMode": "bridge" if network_access else "none",
        "IpcMode": "private",
        "ReadonlyRootfs": True,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges=true"],
        "PidsLimit": 32,
        "Memory": 134217728,
        "MemorySwap": 134217728,
        "ShmSize": 33554432,
        "NanoCpus": 500000000,
    }
    if any(host.get(key) != value for key, value in expected_host.items()):
        raise PermissionError("Docker did not apply the required container isolation limits.")
    if config.get("User") != "65534:65534" or state.get("Running") is not True:
        raise PermissionError("Docker command container is not running as the required user.")

    def allowed_mount(mount: Any) -> bool:
        if not isinstance(mount, dict):
            return False
        value = cast(dict[str, Any], mount)
        return value.get("Type") == "tmpfs" and value.get("Destination") in {
            "/tmp",
            "/workspace",
            "/dev/shm",
        }

    if not all(allowed_mount(mount) for mount in mounts):
        raise PermissionError("Docker command container has an unexpected host or volume mount.")
    # Docker Engine versions differ in whether tmpfs mounts appear in the
    # inspect Mounts array. HostConfig.Tmpfs binds the requested configuration;
    # the fixed in-container readiness probe verifies the effective mounts.


@dataclass
class DockerLocalTransport:
    executable: str
    endpoint: str
    image_reference: str
    profile_sha256: str
    docker_network_access: bool = False
    _control_only: bool = False
    _active: dict[str, asyncio.subprocess.Process] = field(
        default_factory=lambda: dict[str, asyncio.subprocess.Process]()
    )
    _executing: set[str] = field(default_factory=lambda: set[str]())
    _cancelled: set[str] = field(default_factory=lambda: set[str]())
    _receipts: dict[str, dict[str, Any]] = field(
        default_factory=lambda: dict[str, dict[str, Any]]()
    )

    @classmethod
    async def discover(
        cls, authority: CommandGrant | CommandIntent, *, deadline: float
    ) -> DockerLocalTransport:
        if authority.backend != "docker-local-v1" or authority.distribution != "Docker-local":
            raise PermissionError("Docker discovery requires the saved local-daemon grant.")
        endpoint = validate_local_engine_endpoint(_endpoint(), windows=os.name == "nt")
        executable = shutil.which("docker")
        if executable is None:
            raise OSError("Docker CLI is not installed or is missing from PATH.")
        image_reference = validate_image_reference(authority.image_reference or "")
        info = await cls._inspect_info(executable, endpoint, deadline=deadline)
        if isinstance(authority, CommandIntent):
            # Recovery/control must still be possible after the image is removed
            # or the daemon profile changes. It can never authorize a new launch.
            return cls(
                executable,
                endpoint,
                image_reference,
                authority.profile_sha256,
                authority.docker_network_access,
                _control_only=True,
            )
        image = await cls._inspect_image(executable, endpoint, image_reference, deadline=deadline)
        profile = hashlib.sha256(
            json.dumps(
                {
                    "daemon_id": info["ID"],
                    "daemon_profile": {
                        key: info.get(key)
                        for key in (
                            "OperatingSystem",
                            "KernelVersion",
                            "Driver",
                            "SecurityOptions",
                            "CgroupVersion",
                            "CgroupDriver",
                            "DefaultRuntime",
                            "Runtimes",
                        )
                    },
                    "image_reference": image_reference,
                    "image_id": image["Id"],
                    "os_type": info["OSType"],
                    "policy": POLICY_VERSION,
                    "server_version": info["ServerVersion"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            executable,
            endpoint,
            image_reference,
            profile,
            docker_network_access=authority.docker_network_access,
        )

    @staticmethod
    async def _inspect_info(executable: str, endpoint: str, *, deadline: float) -> dict[str, Any]:
        _code, stdout, _stderr = await _cli_request(
            executable,
            endpoint,
            ("info", "--format", "{{json .}}"),
            deadline=deadline,
        )
        try:
            decoded: object = json.loads(stdout)
        except (ValueError, UnicodeError) as exc:
            raise TransportError("Docker daemon returned invalid information.") from exc
        if not isinstance(decoded, dict):
            raise TransportError("Docker daemon returned invalid information.")
        value = cast(dict[str, Any], decoded)
        if (
            not isinstance(value.get("ID"), str)
            or not value["ID"]
            or not isinstance(value.get("ServerVersion"), str)
            or value.get("OSType") != "linux"
        ):
            raise OSError("The selected Docker daemon must run Linux containers.")
        return value

    @staticmethod
    async def _inspect_image(
        executable: str,
        endpoint: str,
        image_reference: str,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        _code, stdout, _stderr = await _cli_request(
            executable,
            endpoint,
            ("image", "inspect", "--format", "{{json .}}", image_reference),
            deadline=deadline,
        )
        try:
            decoded: object = json.loads(stdout)
        except (ValueError, UnicodeError) as exc:
            raise TransportError("Docker returned invalid image metadata.") from exc
        if not isinstance(decoded, dict):
            raise TransportError("Docker returned invalid image metadata.")
        value = cast(dict[str, Any], decoded)
        repo_digests = value.get("RepoDigests")
        config_value = value.get("Config")
        if not isinstance(config_value, dict):
            raise OSError("Docker image metadata has no configuration object.")
        config = cast(dict[str, Any], config_value)
        if (
            not isinstance(value.get("Id"), str)
            or not isinstance(repo_digests, list)
            or image_reference not in repo_digests
            or config.get("Volumes") not in (None, {})
        ):
            raise OSError(
                "The exact Docker image digest must exist locally and declare no implicit volumes."
            )
        return value

    def require_intent(self, intent: CommandIntent) -> None:
        if self._control_only:
            raise PermissionError("A recovery-only Docker transport cannot launch commands.")
        if (
            intent.backend != "docker-local-v1"
            or intent.distribution != "Docker-local"
            or intent.image_reference != self.image_reference
            or intent.profile_sha256 != self.profile_sha256
            or intent.docker_network_access != self.docker_network_access
        ):
            raise PermissionError("Execution grant does not match the Docker image profile.")

    async def verify_profile(self, *, deadline: float) -> None:
        """Run the fixed nonce probe and verify cleanup before reporting readiness."""
        nonce = os.urandom(16).hex()
        snapshot = WorkspaceSnapshot(
            (
                SnapshotFile(
                    "probe.py",
                    f"xaa_network_access = {self.docker_network_access!r}\n{PROBE_CODE}".encode(),
                ),
                SnapshotFile("probe.txt", nonce.encode("ascii")),
            ),
            (),
            (),
        )
        intent = CommandIntent(
            argv=["python3", "-I", "/workspace/probe.py"],
            max_seconds=5,
            execution_id=os.urandom(16).hex(),
            backend="docker-local-v1",
            distribution="Docker-local",
            profile_sha256=self.profile_sha256,
            image_reference=self.image_reference,
            docker_network_access=self.docker_network_access,
            workspace="application-owned-readiness-fixture",
        )
        try:
            result = await self.run(intent, snapshot, deadline=deadline)
            encoded = result.get("stdout_b64")
            if (
                result.get("execution_id") != intent.execution_id
                or result.get("succeeded") is not True
                or result.get("exit_code") != 0
                or result.get("supervisor_exit_code") != 0
                or result.get("termination_reason") != "finished"
                or result.get("cleanup_confirmed") is not True
                or result.get("snapshot_sha256") != snapshot.sha256
                or result.get("filter_sha256") != self.profile_sha256
                or result.get("output_complete") is not True
                or result.get("workspace_changes_persisted") is not False
                or not isinstance(encoded, str)
                or result.get("stdout_bytes", MAX_OUTPUT_BYTES + 1) > 8192
            ):
                raise ValueError("Docker readiness command did not satisfy its execution contract.")
            try:
                decoded: object = json.loads(base64.b64decode(encoded, validate=True))
            except (ValueError, UnicodeError) as exc:
                raise ValueError("Docker readiness probe returned invalid output.") from exc
            if not isinstance(decoded, dict):
                raise TypeError("Docker readiness probe returned invalid output.")
            output = cast(dict[str, Any], decoded)
            if (
                set(output) != {"nonce", "checks", "network_denied"}
                or output.get("nonce") != nonce
                or output.get("checks") is not True
                or output.get("network_denied") is not (not intent.docker_network_access)
            ):
                raise ValueError("Docker readiness probe did not confirm the isolation fixture.")
        finally:

            async def cleanup() -> None:
                cleanup_deadline = time.monotonic() + 20
                acknowledgement = await self.control("cleanup", intent, deadline=cleanup_deadline)
                if acknowledgement.get("cleanup_confirmed") is not True:
                    raise RuntimeError("Docker readiness cleanup is unconfirmed.")
                while True:
                    status = await self.control("status", intent, deadline=cleanup_deadline)
                    if status.get("closed") is True and status.get("manager_alive") is False:
                        return
                    if time.monotonic() >= cleanup_deadline:
                        raise TimeoutError("Docker readiness container is still active.")
                    await asyncio.sleep(0.05)

            await finish_cleanup(cleanup())

    async def _run_cli(
        self,
        arguments: tuple[str, ...],
        *,
        deadline: float,
        payload: bytes = b"",
        output_limit: int = MAX_CLI_RESPONSE_BYTES,
    ) -> bytes:
        code, stdout, _ = await _cli_request(
            self.executable,
            self.endpoint,
            arguments,
            payload=payload,
            deadline=deadline,
            output_limit=output_limit,
        )
        if code != 0:
            raise TransportError("Local Docker operation failed.")
        return stdout

    async def _verify_command_container_profile(
        self, intent: CommandIntent, *, deadline: float
    ) -> None:
        """Verify the actual command container with the fixed, application-owned probe."""
        nonce = os.urandom(16).hex()
        bootstrap = (
            "from pathlib import Path; "
            f"Path('/workspace/probe.txt').write_text({nonce!r}, encoding='ascii'); "
            f"xaa_network_access = {intent.docker_network_access!r}; "
            f"exec({PROBE_CODE!r})"
        )
        output = await self._run_cli(
            (
                "exec",
                "--user",
                "65534:65534",
                docker_container_name(intent.execution_id),
                "python3",
                "-I",
                "-c",
                bootstrap,
            ),
            deadline=deadline,
            output_limit=16 * 1024,
        )
        try:
            decoded: object = json.loads(output)
        except (ValueError, UnicodeError) as exc:
            raise PermissionError("Docker command container failed its isolation probe.") from exc
        if not isinstance(decoded, dict):
            raise PermissionError("Docker command container failed its isolation probe.")
        probe_result = cast(dict[str, Any], decoded)
        if (
            set(probe_result) != {"nonce", "checks", "network_denied"}
            or probe_result.get("nonce") != nonce
            or probe_result.get("checks") is not True
            or probe_result.get("network_denied") is not (not intent.docker_network_access)
        ):
            raise PermissionError("Docker command container failed its isolation probe.")
        cleanup = (
            "from pathlib import Path; "
            "Path('/workspace/probe.txt').unlink(missing_ok=True); "
            "Path('/workspace/probe-write.txt').unlink(missing_ok=True)"
        )
        await self._run_cli(
            (
                "exec",
                "--user",
                "65534:65534",
                docker_container_name(intent.execution_id),
                "python3",
                "-I",
                "-c",
                cleanup,
            ),
            deadline=deadline,
            output_limit=1024,
        )

    async def _inspect_container(
        self, intent: CommandIntent, *, deadline: float
    ) -> dict[str, Any] | None:
        code, stdout, stderr = await _cli_request(
            self.executable,
            self.endpoint,
            ("inspect", "--type", "container", docker_container_name(intent.execution_id)),
            deadline=deadline,
            allow_failure=True,
        )
        if code != 0:
            message = stderr.decode("utf-8", errors="replace").casefold()
            if "no such object" in message or "no such container" in message:
                return None
            raise TransportError("Docker could not inspect the saved container identity.")
        try:
            decoded: object = json.loads(stdout)
        except (ValueError, UnicodeError) as exc:
            raise TransportError("Docker returned invalid container metadata.") from exc
        if not isinstance(decoded, list):
            raise TransportError("Docker returned invalid container metadata.")
        values = cast(list[Any], decoded)
        if len(values) != 1 or not isinstance(values[0], dict):
            raise TransportError("Docker returned invalid container metadata.")
        value = cast(dict[str, Any], values[0])
        config = value.get("Config")
        if not isinstance(config, dict):
            raise TransportError("Docker container metadata has no configuration object.")
        labels = cast(dict[str, Any], config).get("Labels")
        if not isinstance(labels, dict):
            raise PermissionError("Docker container has no execution identity label.")
        labels = cast(dict[str, Any], labels)
        if labels.get(EXECUTION_LABEL) != intent.execution_id:
            raise PermissionError("Docker container identity does not match the saved execution.")
        return value

    async def _remove_container(self, intent: CommandIntent, *, deadline: float) -> bool:
        current = await self._inspect_container(intent, deadline=deadline)
        if current is None:
            return True
        await self._run_cli(
            ("rm", "--force", docker_container_name(intent.execution_id)),
            deadline=deadline,
        )
        return await self._inspect_container(intent, deadline=deadline) is None

    async def run(
        self,
        intent: CommandIntent,
        snapshot: WorkspaceSnapshot,
        *,
        deadline: float,
        on_output: OutputCallback | None = None,
        procedure_secrets: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.require_intent(intent)
        if procedure_secrets or intent.procedure_credential_names:
            raise PermissionError("Docker commands do not receive procedure credentials.")
        if time.monotonic() + intent.max_seconds > deadline:
            raise TimeoutError("Command duration exceeds the remaining parent time budget.")
        create = docker_create_argv(
            intent,
            self.image_reference,
            endpoint=self.endpoint,
            windows=os.name == "nt",
        )
        # The helper's executable is replaced with the resolved local CLI path.
        create = (self.executable, *create[1:])
        started = time.monotonic()
        stdout = bytearray()
        stderr = bytearray()
        process: asyncio.subprocess.Process | None = None
        execution_id: str | None = None
        timed_out = False
        output_limited = False
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Docker execution deadline expired before container creation.")
            with tempfile.TemporaryDirectory(prefix="xaa-docker-cli-") as config:
                process = await asyncio.create_subprocess_exec(
                    *create,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_safe_environment(self.executable, config),
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                self._active[intent.execution_id] = process
                async with asyncio.timeout(min(15, deadline - time.monotonic())):
                    raw_id, _ = await process.communicate()
            if process.returncode != 0:
                raise TransportError("Docker could not create the isolated command container.")
            execution_id = raw_id.decode("ascii", errors="strict").strip()
            if len(execution_id) != 64 or any(
                char not in "0123456789abcdef" for char in execution_id
            ):
                raise TransportError("Docker returned an invalid container identity.")
            await self._run_cli(("start", execution_id), deadline=deadline)
            inspected = await self._inspect_container(intent, deadline=deadline)
            if inspected is None:
                raise TransportError("Docker command container disappeared before policy review.")
            _require_container_policy(inspected, network_access=intent.docker_network_access)
            await self._verify_command_container_profile(intent, deadline=deadline)
            copy = docker_copy_argv(
                intent,
                endpoint=self.endpoint,
                windows=os.name == "nt",
            )
            copy = (self.executable, *copy[1:])
            await self._run_cli(
                copy[3:],
                deadline=deadline,
                payload=_snapshot_tar(snapshot),
                output_limit=16384,
            )
            command = docker_exec_argv(
                intent,
                endpoint=self.endpoint,
                windows=os.name == "nt",
            )
            command = (self.executable, *command[1:])
            with tempfile.TemporaryDirectory(prefix="xaa-docker-cli-") as config:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_safe_environment(self.executable, config),
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                self._active[intent.execution_id] = process
                self._executing.add(intent.execution_id)
                assert process.stdout is not None and process.stderr is not None
                combined = 0

                async def collect(stream_name: str, reader: asyncio.StreamReader) -> bytes:
                    nonlocal combined
                    captured = stdout if stream_name == "stdout" else stderr
                    while chunk := await reader.read(16384):
                        combined += len(chunk)
                        if combined > MAX_OUTPUT_BYTES:
                            raise _DockerOutputLimitExceeded(
                                "Docker command output exceeded the 1 MiB limit."
                            )
                        captured.extend(chunk)
                        if on_output is not None:
                            await on_output(stream_name, chunk)
                    return bytes(captured)

                readers = [
                    asyncio.create_task(collect("stdout", process.stdout)),
                    asyncio.create_task(collect("stderr", process.stderr)),
                ]
                waiter = asyncio.create_task(process.wait())
                try:
                    async with asyncio.timeout(
                        min(intent.max_seconds, deadline - time.monotonic())
                    ):
                        await asyncio.gather(*readers)
                        await waiter
                except TimeoutError:
                    timed_out = True
                    await stop_owned_process(process)
                except _DockerOutputLimitExceeded:
                    output_limited = True
                    await stop_owned_process(process)
                finally:
                    for reader in readers:
                        if not reader.done():
                            reader.cancel()
                    await asyncio.gather(*readers, return_exceptions=True)
                    if not waiter.done():
                        waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)
                exit_code = process.returncode if process.returncode is not None else 125
            oom_killed = False
            container = await self._inspect_container(intent, deadline=deadline)
            if container is not None:
                state_value = container.get("State")
                state = cast(dict[str, Any], state_value) if isinstance(state_value, dict) else {}
                oom_killed = state.get("OOMKilled") is True
            cancelled = intent.execution_id in self._cancelled
            termination = (
                "cancelled"
                if cancelled
                else "output_limit"
                if output_limited
                else "timeout"
                if timed_out
                else "oom-kill"
                if oom_killed
                else "finished"
            )
            normal = termination == "finished"
            result: dict[str, Any] = {
                "execution_id": intent.execution_id,
                "snapshot_sha256": snapshot.sha256,
                "filter_sha256": self.profile_sha256,
                "termination_reason": termination,
                "exit_code": exit_code if normal else None,
                "supervisor_exit_code": exit_code,
                "succeeded": normal and exit_code == 0,
                "output_complete": normal,
                "cleanup_confirmed": True,
                "workspace_changes_persisted": False,
                "stdout_b64": base64.b64encode(stdout).decode("ascii"),
                "stderr_b64": base64.b64encode(stderr).decode("ascii"),
                "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
                "elapsed_seconds": max(0.0, time.monotonic() - started),
            }
            if not await self._remove_container(intent, deadline=time.monotonic() + 15):
                raise TransportError("Docker command container cleanup could not be confirmed.")
            self._active.pop(intent.execution_id, None)
            self._executing.discard(intent.execution_id)
            self._cancelled.discard(intent.execution_id)
            self._receipts[intent.execution_id] = result
            return result
        except BaseException:
            if process is not None and process.returncode is None:
                await stop_owned_process(process)
            try:
                await self._remove_container(intent, deadline=time.monotonic() + 15)
            except (OSError, RuntimeError, TimeoutError, TransportError):
                pass
            self._active.pop(intent.execution_id, None)
            self._executing.discard(intent.execution_id)
            self._cancelled.discard(intent.execution_id)
            raise

    async def control(
        self,
        action: Literal["stop", "cleanup", "status"],
        intent: CommandIntent,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        if action not in {"stop", "cleanup", "status"}:
            raise ValueError("Unsupported Docker control action.")
        if (
            intent.backend != "docker-local-v1"
            or intent.distribution != "Docker-local"
            or intent.image_reference != self.image_reference
        ):
            raise PermissionError("Docker control must use the saved execution identity.")
        process = self._active.get(intent.execution_id)
        if action == "stop" and intent.execution_id in self._executing:
            self._cancelled.add(intent.execution_id)
        if action in {"stop", "cleanup"} and process is not None and process.returncode is None:
            await stop_owned_process(process)
        if action == "stop":
            current = await self._inspect_container(intent, deadline=deadline)
            if current is not None:
                state_value = current.get("State")
                state = cast(dict[str, Any], state_value) if isinstance(state_value, dict) else {}
                if state.get("Running") is True:
                    try:
                        await self._run_cli(
                            ("kill", docker_container_name(intent.execution_id)),
                            deadline=deadline,
                        )
                    except TransportError:
                        observed = await self._inspect_container(intent, deadline=deadline)
                        observed_state = (
                            cast(dict[str, Any], observed.get("State"))
                            if observed is not None and isinstance(observed.get("State"), dict)
                            else {}
                        )
                        if observed is not None and observed_state.get("Running") is True:
                            raise
        if action == "cleanup":
            removed = await self._remove_container(intent, deadline=deadline)
        else:
            removed = False
        container = await self._inspect_container(intent, deadline=deadline)
        closed = container is None
        running = False
        if container is not None:
            state_value = container.get("State")
            state = cast(dict[str, Any], state_value) if isinstance(state_value, dict) else {}
            running = state.get("Running") is True
        return {
            "execution_id": intent.execution_id,
            "closed": closed,
            "cleanup_confirmed": closed
            if action == "cleanup"
            else process is None or process.returncode is not None,
            "manager_alive": running or (process is not None and process.returncode is None),
            "outcome": (
                {"state": "finished", "result": self._receipts[intent.execution_id]}
                if intent.execution_id in self._receipts
                else None
            ),
            "removed": removed,
        }

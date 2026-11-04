"""Explicit fixed sandbox self-test; never accepts project code or grants permission."""

import asyncio
import base64
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.cleanup import finish_cleanup
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport, profile_fingerprint, response_object

CHECKS = (
    "private_input",
    "unprivileged_identity",
    "seccomp_and_capabilities",
    "readonly_runtime",
    "private_tmpfs",
    "host_mounts_absent",
    "credential_environment_absent",
    "socket_families_denied",
    "nested_namespaces_denied",
    "kernel_resource_gate",
    "cleanup_confirmed",
)
SUPPORTED = os.name == "nt"
PROBE = """
import ctypes,errno,json,os,pathlib,socket
nonce=pathlib.Path('probe.txt').read_text()
assert os.getuid()==65534 and os.getgid()==65534
fields=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert fields['NoNewPrivs'].strip()=='1' and fields['Seccomp'].strip()=='2'
assert int(fields['CapEff'].strip(),16)==0
assert all(os.statvfs(p).f_flag & os.ST_RDONLY for p in ('/','/usr','/input'))
assert all(not os.statvfs(p).f_flag & os.ST_RDONLY and os.statvfs(p).f_blocks*os.statvfs(p).f_frsize<=33554432 for p in ('/tmp','/workspace'))
assert not any(pathlib.Path(p).exists() for p in ('/mnt/c','/init','/root','/run','/sys'))
assert set(os.environ)<= {'PATH','LANG','HOME','LC_CTYPE','PWD'}
assert os.environ.get('PWD')=='/workspace'
for family in (socket.AF_INET,socket.AF_INET6,socket.AF_UNIX,socket.AF_VSOCK):
    try:
        value=socket.socket(family,socket.SOCK_STREAM)
    except OSError as exc:
        assert exc.errno==errno.EPERM
    else:
        value.close()
        raise RuntimeError('socket creation was permitted')
libc=ctypes.CDLL(None,use_errno=True)
assert libc.unshare(0x10000000)==-1 and ctypes.get_errno()==errno.EPERM
pathlib.Path('private-write.txt').write_text(nonce)
assert pathlib.Path('/input/probe.txt').read_text()==nonce
print(json.dumps({'nonce':nonce,'checks':True}))
"""


class ReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    distribution: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_seconds: Literal[30] = 30
    max_active_executions_per_distribution: Literal[2] = 2
    memory_bytes: Literal[134217728] = 134217728
    tasks: Literal[32] = 32
    cpu_percent: Literal[50] = 50
    output_bytes: Literal[1048576] = 1048576
    private_workspace_bytes: Literal[33554432] = 33554432
    network: Literal["denied"] = "denied"
    workspace_changes: Literal["private_copy_only"] = "private_copy_only"


class ReadinessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    backend: Literal["wsl-bubblewrap-v1"] = "wsl-bubblewrap-v1"
    distribution: str
    profile_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["ready", "unavailable", "unsupported", "busy"]
    checked_at: datetime
    expires_at: datetime
    checks: list[str]
    policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    error_code: (
        Literal["profile_unavailable", "unsupported_platform", "backend_probe_failed", "probe_busy"]
        | None
    ) = None


class ExecutionReadiness:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.cache: dict[str, tuple[float, ReadinessResult]] = {}

    def require_grant(self, grant: CommandGrant) -> None:
        """Accept only a current successful server-observed check, never client claims."""
        cached = self.cache.get(grant.distribution)
        try:
            digest = profile_fingerprint()[0]
        except OSError as exc:
            raise PermissionError(
                "Sandbox profile is unavailable. Check the sandbox again."
            ) from exc
        if (
            not SUPPORTED
            or cached is None
            or cached[0] <= time.monotonic()
            or cached[1].expires_at <= datetime.now(UTC)
            or cached[1].state != "ready"
            or cached[1].profile_sha256 != grant.profile_sha256
            or grant.profile_sha256 != digest
            or tuple(cached[1].checks) != CHECKS
        ):
            raise PermissionError("A current successful sandbox check is required for this grant.")

    async def check(self, request: ReadinessRequest) -> ReadinessResult:
        distribution = request.distribution
        now = datetime.now(UTC)
        digest: str | None = None
        try:
            digest = profile_fingerprint()[0]
        except OSError:
            return ReadinessResult(
                distribution=distribution,
                profile_sha256=None,
                state="unavailable",
                checked_at=now,
                expires_at=now,
                checks=[],
                error_code="profile_unavailable",
            )
        cached = self.cache.get(distribution)
        if cached and cached[0] > time.monotonic() and cached[1].profile_sha256 == digest:
            return cached[1]
        if not SUPPORTED:
            return ReadinessResult(
                distribution=distribution,
                profile_sha256=digest,
                state="unsupported",
                checked_at=now,
                expires_at=now,
                checks=[],
                error_code="unsupported_platform",
            )
        if self.lock.locked():
            return ReadinessResult(
                distribution=distribution,
                profile_sha256=digest,
                state="busy",
                checked_at=now,
                expires_at=now,
                checks=[],
                error_code="probe_busy",
            )
        async with self.lock:
            ready = False
            try:
                transport = await WSLTransport.discover(
                    distribution, deadline=time.monotonic() + 30
                )
                if transport.profile_sha256 != digest:
                    raise PermissionError("Profile changed during readiness discovery.")
                nonce = uuid4().hex
                snapshot = WorkspaceSnapshot((SnapshotFile("probe.txt", nonce.encode()),), (), ())
                intent = CommandIntent(
                    argv=["/usr/bin/python3", "-I", "-c", PROBE],
                    max_seconds=5,
                    execution_id=uuid4().hex,
                    backend="wsl-bubblewrap-v1",
                    distribution=distribution,
                    profile_sha256=digest,
                    workspace="application-owned-readiness-fixture",
                )
                try:
                    result = await transport.run(intent, snapshot, deadline=time.monotonic() + 15)
                    encoded = result.get("stdout_b64")
                    expected_limits = {
                        "max_active_executions": 2,
                        "memory_bytes": 134217728,
                        "cpu_percent": 50,
                        "tasks": 32,
                        "scratch_bytes": 33554432,
                        "workspace_bytes": 33554432,
                        "output_bytes": 1048576,
                        "seconds": 5,
                        "network": "none",
                    }
                    if (
                        result.get("succeeded") is not True
                        or result.get("cleanup_confirmed") is not True
                        or type(result.get("exit_code")) is not int
                        or result["exit_code"] != 0
                        or result.get("snapshot_sha256") != snapshot.sha256
                        or result.get("backend") != "wsl-bubblewrap-v1"
                        or result.get("output_complete") is not True
                        or result.get("workspace_changes_persisted") is not False
                        or result.get("limits") != expected_limits
                        or not isinstance(encoded, str)
                        or len(encoded) > 16384
                    ):
                        raise ValueError("Sandbox self-test did not finish successfully.")
                    output = response_object(base64.b64decode(encoded, validate=True))
                    if (
                        set(output) != {"nonce", "checks"}
                        or output["nonce"] != nonce
                        or output["checks"] is not True
                    ):
                        raise ValueError("Sandbox self-test output did not match its fixture.")
                finally:

                    async def cleanup() -> None:
                        deadline = time.monotonic() + 20
                        acknowledgement = await transport.control(
                            "cleanup", intent, deadline=deadline
                        )
                        if acknowledgement.get("cleanup_confirmed") is not True:
                            raise RuntimeError("Readiness cleanup is unconfirmed.")
                        while True:
                            status = await transport.control("status", intent, deadline=deadline)
                            if (
                                status.get("closed") is True
                                and status.get("manager_alive") is False
                            ):
                                return
                            if time.monotonic() >= deadline:
                                raise TimeoutError("Readiness manager is still active.")
                            await asyncio.sleep(0.05)

                    await finish_cleanup(cleanup())
                ready = profile_fingerprint()[0] == digest
            except (OSError, RuntimeError, ValueError):
                ready = False
            now = datetime.now(UTC)
            ttl = 60 if ready else 10
            answer = ReadinessResult(
                distribution=distribution,
                profile_sha256=digest,
                state="ready" if ready else "unavailable",
                checked_at=now,
                expires_at=now + timedelta(seconds=ttl),
                checks=list(CHECKS) if ready else [],
                error_code=None if ready else "backend_probe_failed",
            )
            self.cache = {
                key: value for key, value in self.cache.items() if value[0] > time.monotonic()
            }
            if len(self.cache) >= 8:
                self.cache.pop(next(iter(self.cache)))
            self.cache[distribution] = (time.monotonic() + ttl, answer)
            return answer

"""Explicit fixed sandbox self-test; never accepts project code or grants permission."""

import asyncio
import base64
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator

from xaa.runtime.cleanup import finish_cleanup
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.windows_local_probe import (
    PROBE_CODE as WINDOWS_LOCAL_PROBE_CODE,
)
from xaa.runtime.windows_local_probe import (
    command_argv as windows_local_probe_argv,
)
from xaa.runtime.windows_local_transport import (
    WINDOWS_LOCAL_DISTRIBUTION,
    WindowsLocalTransport,
)
from xaa.runtime.windows_local_transport import (
    profile_fingerprint as windows_local_profile_fingerprint,
)
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
WINDOWS_LOCAL_CHECKS = (
    "appcontainer_identity",
    "network_capabilities_absent",
    "private_snapshot",
    "workspace_write_isolated",
    "private_runtime_temp",
    "credential_environment_absent",
    "bounded_child_process_tree",
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
DEPENDENCY_PROBE = """
import importlib.metadata,json,os,pathlib,platform,sys
request=json.loads(pathlib.Path('probe.json').read_text())
names=request['names']
versions={}
for name in names:
    try:
        versions[name]=importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        versions[name]=None
implementation_version=sys.implementation.version
print(json.dumps({'versions':versions,'environment':{
    'implementation_name':sys.implementation.name,
    'implementation_version':'.'.join(str(value) for value in implementation_version[:3]),
    'os_name':os.name,
    'platform_machine':platform.machine(),
    'platform_python_implementation':platform.python_implementation(),
    'platform_release':platform.release(),
    'platform_system':platform.system(),
    'platform_version':platform.version(),
    'python_full_version':platform.python_version(),
    'python_version':'.'.join(str(value) for value in sys.version_info[:2]),
    'sys_platform':sys.platform
}}))
"""


class ReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    backend: Literal["wsl-bubblewrap-v1", "windows-appcontainer-v1"] = "wsl-bubblewrap-v1"
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
    backend: Literal["wsl-bubblewrap-v1", "windows-appcontainer-v1"] = "wsl-bubblewrap-v1"
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


class SkillDependencyReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    distribution: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    python_dependency_specs: list[str] = Field(max_length=64)

    @field_validator("python_dependency_specs")
    @classmethod
    def validate_specs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Python dependency declarations must be unique.")
        for value in values:
            if not value or len(value) > 256:
                raise ValueError("Python dependency declaration is invalid.")
            try:
                requirement = Requirement(value)
            except InvalidRequirement as exc:
                raise ValueError("Python dependency declaration is invalid.") from exc
            if requirement.url is not None:
                raise ValueError("Direct dependency references cannot be checked.")
        return values


class SkillDependencyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirement: str
    status: Literal["available", "missing", "incompatible", "not_applicable", "unchecked"]
    installed_version: str | None = None


class SkillDependencyReadinessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    distribution: str
    state: Literal["ready", "unavailable", "unsupported", "busy", "no_dependencies"]
    python_version: str | None = None
    dependencies: list[SkillDependencyStatus]
    unverified_dependency_count: int = Field(ge=0, le=256)
    checked_at: datetime
    error_code: (
        Literal[
            "profile_unavailable",
            "unsupported_platform",
            "sandbox_not_ready",
            "probe_busy",
            "dependency_probe_failed",
        ]
        | None
    ) = None


class ExecutionReadiness:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.cache: dict[tuple[str, str], tuple[float, ReadinessResult]] = {}

    def require_grant(self, grant: CommandGrant) -> None:
        """Accept only a current successful server-observed check, never client claims."""
        if grant.backend == "windows-appcontainer-v1":
            try:
                digest = windows_local_profile_fingerprint()
            except OSError as exc:
                raise PermissionError(
                    "Windows command isolation is unavailable. Check the sandbox again."
                ) from exc
            cached = self.cache.get((grant.backend, grant.distribution))
            if (
                grant.distribution != WINDOWS_LOCAL_DISTRIBUTION
                or not SUPPORTED
                or cached is None
                or cached[0] <= time.monotonic()
                or cached[1].expires_at <= datetime.now(UTC)
                or cached[1].state != "ready"
                or cached[1].backend != grant.backend
                or cached[1].profile_sha256 != grant.profile_sha256
                or grant.profile_sha256 != digest
                or tuple(cached[1].checks) != WINDOWS_LOCAL_CHECKS
            ):
                raise PermissionError(
                    "A current successful Windows AppContainer check is required for this grant."
                )
            return
        cached = self.cache.get((grant.backend, grant.distribution))
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

    async def check_python_dependencies(
        self,
        request: SkillDependencyReadinessRequest,
        *,
        unverified_dependency_count: int = 0,
    ) -> SkillDependencyReadinessResult:
        """Check declared Python distributions in the isolated WSL interpreter without importing them."""
        if not 0 <= unverified_dependency_count <= 256:
            raise ValueError("Unverified dependency count is out of range.")
        now = datetime.now(UTC)
        if not request.python_dependency_specs:
            return SkillDependencyReadinessResult(
                distribution=request.distribution,
                state="no_dependencies",
                dependencies=[],
                unverified_dependency_count=unverified_dependency_count,
                checked_at=now,
            )

        sandbox = await self.check(ReadinessRequest(distribution=request.distribution))
        if sandbox.state != "ready":
            mapped_state: Literal["unavailable", "unsupported", "busy"] = (
                sandbox.state
                if sandbox.state in {"unavailable", "unsupported", "busy"}
                else "unavailable"
            )
            error_code: Literal[
                "profile_unavailable",
                "unsupported_platform",
                "sandbox_not_ready",
                "probe_busy",
                "dependency_probe_failed",
            ] = "sandbox_not_ready"
            if sandbox.error_code == "profile_unavailable":
                error_code = "profile_unavailable"
            elif sandbox.error_code == "unsupported_platform":
                error_code = "unsupported_platform"
            elif sandbox.error_code == "probe_busy":
                error_code = "probe_busy"
            return SkillDependencyReadinessResult(
                distribution=request.distribution,
                state=mapped_state,
                dependencies=[
                    SkillDependencyStatus(requirement=spec, status="unchecked")
                    for spec in request.python_dependency_specs
                ],
                unverified_dependency_count=unverified_dependency_count,
                checked_at=now,
                error_code=error_code,
            )
        if self.lock.locked():
            return SkillDependencyReadinessResult(
                distribution=request.distribution,
                state="busy",
                dependencies=[
                    SkillDependencyStatus(requirement=spec, status="unchecked")
                    for spec in request.python_dependency_specs
                ],
                unverified_dependency_count=unverified_dependency_count,
                checked_at=now,
                error_code="probe_busy",
            )

        requirements = [Requirement(spec) for spec in request.python_dependency_specs]
        names = sorted({requirement.name for requirement in requirements}, key=str.casefold)
        dependency_file = json.dumps({"names": names}, separators=(",", ":")).encode("utf-8")
        digest = sandbox.profile_sha256
        assert digest is not None
        versions: dict[str, str | None] | None = None
        environment: dict[str, str] | None = None
        try:
            async with self.lock:
                transport = await WSLTransport.discover(
                    request.distribution, deadline=time.monotonic() + 30
                )
                if transport.profile_sha256 != digest or profile_fingerprint()[0] != digest:
                    raise PermissionError("Execution profile changed during dependency discovery.")
                snapshot = WorkspaceSnapshot((SnapshotFile("probe.json", dependency_file),), (), ())
                intent = CommandIntent(
                    argv=["/usr/bin/python3", "-I", "-c", DEPENDENCY_PROBE],
                    max_seconds=5,
                    execution_id=uuid4().hex,
                    backend="wsl-bubblewrap-v1",
                    distribution=request.distribution,
                    profile_sha256=digest,
                    workspace="application-owned-skill-dependency-probe",
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
                        or len(encoded) > 87_384
                    ):
                        raise ValueError("Skill dependency probe did not finish successfully.")
                    output = response_object(base64.b64decode(encoded, validate=True))
                    raw_versions_value = output.get("versions")
                    raw_environment_value = output.get("environment")
                    expected_environment = {
                        "implementation_name",
                        "implementation_version",
                        "os_name",
                        "platform_machine",
                        "platform_python_implementation",
                        "platform_release",
                        "platform_system",
                        "platform_version",
                        "python_full_version",
                        "python_version",
                        "sys_platform",
                    }
                    if (
                        set(output) != {"versions", "environment"}
                        or not isinstance(raw_versions_value, dict)
                        or not isinstance(raw_environment_value, dict)
                    ):
                        raise ValueError("Skill dependency probe returned invalid metadata.")
                    raw_versions = cast(dict[str, object], raw_versions_value)
                    raw_environment = cast(dict[str, object], raw_environment_value)
                    if (
                        set(raw_versions) != set(names)
                        or any(
                            version is not None and not isinstance(version, str)
                            for version in raw_versions.values()
                        )
                        or set(raw_environment) != expected_environment
                        or any(not isinstance(value, str) for value in raw_environment.values())
                    ):
                        raise ValueError("Skill dependency probe returned invalid metadata.")
                    versions = {
                        str(name): value if isinstance(value, str) else None
                        for name, value in raw_versions.items()
                    }
                    environment = {str(name): str(value) for name, value in raw_environment.items()}
                    if profile_fingerprint()[0] != digest:
                        raise PermissionError("Execution profile changed during dependency probe.")
                finally:

                    async def cleanup() -> None:
                        deadline = time.monotonic() + 20
                        acknowledgement = await transport.control(
                            "cleanup", intent, deadline=deadline
                        )
                        if acknowledgement.get("cleanup_confirmed") is not True:
                            raise RuntimeError("Dependency probe cleanup is unconfirmed.")
                        while True:
                            status = await transport.control("status", intent, deadline=deadline)
                            if (
                                status.get("closed") is True
                                and status.get("manager_alive") is False
                            ):
                                return
                            if time.monotonic() >= deadline:
                                raise TimeoutError("Dependency probe manager remains active.")
                            await asyncio.sleep(0.05)

                    await finish_cleanup(cleanup())
        except (OSError, RuntimeError, ValueError, TypeError, KeyError):
            versions = None
            environment = None

        if versions is None or environment is None:
            return SkillDependencyReadinessResult(
                distribution=request.distribution,
                state="unavailable",
                dependencies=[
                    SkillDependencyStatus(requirement=spec, status="unchecked")
                    for spec in request.python_dependency_specs
                ],
                unverified_dependency_count=unverified_dependency_count,
                checked_at=datetime.now(UTC),
                error_code="dependency_probe_failed",
            )

        marker_environment = cast(dict[str, str], default_environment())
        marker_environment.update(environment)
        statuses: list[SkillDependencyStatus] = []
        for spec, requirement in zip(request.python_dependency_specs, requirements, strict=True):
            if requirement.marker is not None and not requirement.marker.evaluate(
                marker_environment
            ):
                statuses.append(SkillDependencyStatus(requirement=spec, status="not_applicable"))
                continue
            installed = versions.get(requirement.name)
            if installed is None:
                statuses.append(SkillDependencyStatus(requirement=spec, status="missing"))
                continue
            try:
                available = not requirement.specifier or Version(installed) in requirement.specifier
            except InvalidVersion:
                statuses.append(
                    SkillDependencyStatus(
                        requirement=spec, status="unchecked", installed_version=installed
                    )
                )
                continue
            statuses.append(
                SkillDependencyStatus(
                    requirement=spec,
                    status="available" if available else "incompatible",
                    installed_version=installed,
                )
            )
        return SkillDependencyReadinessResult(
            distribution=request.distribution,
            state="ready",
            python_version=environment["python_full_version"],
            dependencies=statuses,
            unverified_dependency_count=unverified_dependency_count,
            checked_at=datetime.now(UTC),
        )

    async def check(self, request: ReadinessRequest) -> ReadinessResult:
        if request.backend == "windows-appcontainer-v1":
            return await self._check_windows_local(request.distribution)
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
        cache_key = (request.backend, distribution)
        cached = self.cache.get(cache_key)
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
            self.cache[cache_key] = (time.monotonic() + ttl, answer)
            return answer

    async def _check_windows_local(self, distribution: str) -> ReadinessResult:
        now = datetime.now(UTC)
        try:
            digest = windows_local_profile_fingerprint()
        except OSError:
            return ReadinessResult(
                backend="windows-appcontainer-v1",
                distribution=distribution,
                profile_sha256=None,
                state="unavailable",
                checked_at=now,
                expires_at=now,
                checks=[],
                error_code="profile_unavailable",
            )
        cache_key = ("windows-appcontainer-v1", distribution)
        cached = self.cache.get(cache_key)
        if cached and cached[0] > time.monotonic() and cached[1].profile_sha256 == digest:
            return cached[1]
        if distribution != WINDOWS_LOCAL_DISTRIBUTION or not SUPPORTED:
            return ReadinessResult(
                backend="windows-appcontainer-v1",
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
                backend="windows-appcontainer-v1",
                distribution=distribution,
                profile_sha256=digest,
                state="busy",
                checked_at=now,
                expires_at=now,
                checks=[],
                error_code="probe_busy",
            )
        async with self.lock:
            nonce = uuid4().hex

            async def discard_probe_connection(
                _reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                writer.close()

            loopback_server: asyncio.Server | None = None
            snapshot = WorkspaceSnapshot(
                (
                    SnapshotFile("probe.py", WINDOWS_LOCAL_PROBE_CODE.encode()),
                    SnapshotFile("probe.txt", nonce.encode()),
                ),
                (),
                (),
            )
            ready = False
            try:
                transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
                loopback_server = await asyncio.start_server(
                    discard_probe_connection, "127.0.0.1", 0
                )
                port = loopback_server.sockets[0].getsockname()[1] if loopback_server.sockets else 0
                intent = CommandIntent(
                    argv=windows_local_probe_argv(
                        sys.executable, port, frozen=bool(getattr(sys, "frozen", False))
                    ),
                    max_seconds=5,
                    execution_id=uuid4().hex,
                    backend="windows-appcontainer-v1",
                    distribution=WINDOWS_LOCAL_DISTRIBUTION,
                    profile_sha256=digest,
                    workspace="application-owned-readiness-fixture",
                )
                result = await transport.run(intent, snapshot, deadline=time.monotonic() + 15)
                try:
                    output = json.loads(base64.b64decode(result["stdout_b64"], validate=True))
                except (json.JSONDecodeError, ValueError):
                    stderr = base64.b64decode(result.get("stderr_b64", ""), validate=True)
                    stderr_excerpt = stderr.decode("utf-8", errors="replace")[:240]
                    local_data = os.environ.get("LOCALAPPDATA", "")
                    if local_data:
                        stderr_excerpt = stderr_excerpt.replace(local_data, "<local-app-data>")
                    logging.getLogger(__name__).warning(
                        "Windows AppContainer probe output was not JSON "
                        "(succeeded=%s; exit=%s; stdout_bytes=%s; stderr_bytes=%s; stderr=%r).",
                        result.get("succeeded"),
                        result.get("exit_code"),
                        result.get("stdout_bytes"),
                        result.get("stderr_bytes"),
                        stderr_excerpt,
                    )
                    raise
                ready = (
                    result.get("execution_id") == intent.execution_id
                    and result.get("succeeded") is True
                    and result.get("cleanup_confirmed") is True
                    and result.get("snapshot_sha256") == snapshot.sha256
                    and result.get("output_complete") is True
                    and result.get("workspace_changes_persisted") is False
                    and output
                    == {
                        "nonce": nonce,
                        "appcontainer": True,
                        "network_denied": True,
                        "private_temp": True,
                        "checks": True,
                    }
                    and windows_local_profile_fingerprint() == digest
                )
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, TimeoutError) as exc:
                failed_path = getattr(exc, "filename", None)
                failed_name = Path(failed_path).name if isinstance(failed_path, str) else None
                error_detail = str(exc)[:240]
                local_data = os.environ.get("LOCALAPPDATA", "")
                if local_data:
                    error_detail = error_detail.replace(local_data, "<local-app-data>")
                logging.getLogger(__name__).warning(
                    "Windows AppContainer readiness probe failed "
                    "(%s; winerror=%s; file=%s; detail=%s).",
                    type(exc).__name__,
                    getattr(exc, "winerror", None),
                    failed_name,
                    error_detail,
                )
                ready = False
            finally:
                if loopback_server is not None:
                    loopback_server.close()
                    await loopback_server.wait_closed()
            observed = datetime.now(UTC)
            ttl = 60 if ready else 10
            answer = ReadinessResult(
                backend="windows-appcontainer-v1",
                distribution=distribution,
                profile_sha256=digest,
                state="ready" if ready else "unavailable",
                checked_at=observed,
                expires_at=observed + timedelta(seconds=ttl),
                checks=list(WINDOWS_LOCAL_CHECKS) if ready else [],
                error_code=None if ready else "backend_probe_failed",
            )
            self.cache = {
                key: value for key, value in self.cache.items() if value[0] > time.monotonic()
            }
            if len(self.cache) >= 8:
                self.cache.pop(next(iter(self.cache)))
            self.cache[cache_key] = (time.monotonic() + ttl, answer)
            return answer

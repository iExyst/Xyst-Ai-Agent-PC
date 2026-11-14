"""Run commands in a private snapshot inside a no-network Windows AppContainer."""

from __future__ import annotations

import asyncio
import hashlib
import json
import marshal
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.mcp_appcontainer import (
    BOOTSTRAP_PREFIX,
    ERROR_FRAME_PREFIX,
    FILE_GENERIC_EXECUTE,
    FILE_GENERIC_READ,
    FILE_GENERIC_WRITE,
    READY_FRAME,
    AppContainerAccess,
    appcontainer_shim_fingerprint,
    process_identity,
    startup_error_message,
)
from xaa.runtime.processes import stop_owned_process
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot, materialize_snapshot

WINDOWS_LOCAL_DISTRIBUTION = "Windows-Local"
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_INTERACTIVE_FRAME_BYTES = 3 * 1024 * 1024
MAX_PRIVATE_WORKSPACE_BYTES = 32 * 1024 * 1024
MAX_PRIVATE_RUNTIME_BYTES = 256 * 1024 * 1024
MAX_PRIVATE_COMMAND_BYTES = MAX_PRIVATE_WORKSPACE_BYTES + MAX_PRIVATE_RUNTIME_BYTES
MAX_SHIM_RUNTIME_BYTES = 192 * 1024 * 1024
MAX_PRIVATE_WORKSPACE_ENTRIES = 50_000
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _new_process_map() -> dict[str, asyncio.subprocess.Process]:
    return {}


def _new_receipt_map() -> dict[str, dict[str, Any]]:
    return {}


def profile_fingerprint() -> str:
    """Bind grants to the AppContainer launcher source and current Python runtime."""
    shim_fingerprint = appcontainer_shim_fingerprint()
    executable = Path(sys.executable).resolve()
    runtime_hash = hashlib.sha256()
    with executable.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            runtime_hash.update(chunk)
    source_paths = (
        Path(__file__).resolve(),
        Path(__file__).resolve().parents[1] / "extensions" / "pypi_resolver.py",
        Path(__file__).resolve().parents[1] / "extensions" / "pypi_resolver_worker.py",
        Path(__file__).resolve().parents[1] / "extensions" / "pypi_wheels.py",
        Path(__file__).resolve().parents[1] / "extensions" / "pypi_wheel_worker.py",
    )
    source_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
        if path.is_file()
    }
    if not source_hashes and getattr(sys, "frozen", False):
        source_hashes = {
            "windows_local_transport.run": hashlib.sha256(
                marshal.dumps(WindowsLocalTransport.run.__code__)
            ).hexdigest()
        }
    record = {
        "shim_sha256": shim_fingerprint,
        "runtime": str(executable),
        "runtime_sha256": runtime_hash.hexdigest(),
        "worker_source_sha256": source_hashes,
        "python": list(sys.version_info[:3]),
        "backend": "windows-appcontainer-v1",
        "no_network": True,
        "private_snapshot": True,
    }
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()


def _private_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise OSError("Windows local application storage is unavailable.")
    root = Path(local_app_data) / "Xyst" / "XAA" / "command-staging"
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise PermissionError("Windows command staging must be a real local directory.")
    return root.resolve(strict=True)


def _journal_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise OSError("Windows local application storage is unavailable.")
    root = Path(local_app_data) / "Xyst" / "XAA" / "appcontainer"
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise PermissionError("Windows isolation records must use a real local directory.")
    return root.resolve(strict=True)


def _restricted_environment(executable: Path, temporary: Path) -> dict[str, str]:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PUBLIC",
        "OS",
        "PROCESSOR_ARCHITECTURE",
    }
    env = {key.upper(): value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(
        {
            "SYSTEMROOT": system_root,
            "WINDIR": system_root,
            "PATH": os.pathsep.join((str(executable.parent), str(Path(system_root) / "System32"))),
            "TEMP": str(temporary),
            "TMP": str(temporary),
        }
    )
    return env


def _resolve_executable(value: str) -> Path:
    candidate = Path(value)
    if candidate.suffix.casefold() in {".cmd", ".bat", ".ps1"}:
        raise PermissionError("Windows local commands must start a directly executable program.")
    found = (
        str(candidate)
        if candidate.is_absolute() or candidate.parent != Path(".")
        else shutil.which(value)
    )
    if not found:
        raise FileNotFoundError("The requested Windows program is not installed or not on PATH.")
    executable = Path(found).resolve(strict=True)
    if not executable.is_file() or executable.suffix.casefold() in {".cmd", ".bat", ".ps1"}:
        raise PermissionError("Windows local commands must start a directly executable program.")
    if executable.suffix.casefold() != ".exe":
        raise PermissionError("Windows local commands must use an .exe program.")
    return executable


def _trusted_executable_directory(executable: Path) -> bool:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    roots = [system_root, Path(sys.prefix), Path(sys.base_prefix)]
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "Programs")
    resolved = executable.resolve(strict=True)
    return any(
        resolved.is_relative_to(root.resolve(strict=True)) for root in roots if root.exists()
    )


def _private_workspace_size(root: Path, *, max_bytes: int = MAX_PRIVATE_WORKSPACE_BYTES) -> int:
    pending = [root]
    total = 0
    entries = 0
    while pending:
        directory = pending.pop()
        try:
            children_context = os.scandir(directory)
        except FileNotFoundError:
            if directory == root:
                raise
            # Frozen one-file runtimes create and remove extraction files while
            # their process tree is running. A nested directory can disappear
            # after its parent entry was observed; that transient race is safe
            # to skip because it no longer consumes private storage.
            continue
        with children_context as children:
            for child in children:
                entries += 1
                if entries > MAX_PRIVATE_WORKSPACE_ENTRIES:
                    raise ValueError("Windows command workspace exceeded its entry limit.")
                try:
                    info = child.stat(follow_symlinks=False)
                except FileNotFoundError:
                    # PyInstaller's one-file extraction tree is volatile while
                    # a frozen child starts or exits. Missing entries no longer
                    # consume the bounded private-storage budget.
                    continue
                if info.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError("Windows command workspaces cannot contain reparse points.")
                if child.is_dir(follow_symlinks=False):
                    pending.append(Path(child.path))
                else:
                    total += info.st_size
                    if total > max_bytes:
                        raise ValueError("Windows command private storage exceeded its size limit.")
    return total


@dataclass
class WindowsLocalTransport:
    """A direct executable launched with AppContainer filesystem grants only."""

    profile_sha256: str
    _active: dict[str, asyncio.subprocess.Process] = field(default_factory=_new_process_map)
    _receipts: dict[str, dict[str, Any]] = field(default_factory=_new_receipt_map)

    @classmethod
    async def discover(cls, *, deadline: float) -> WindowsLocalTransport:
        if time.monotonic() >= deadline:
            raise TimeoutError("Windows sandbox discovery deadline expired.")
        if os.name != "nt":
            raise OSError("Windows AppContainer command isolation requires Windows.")
        return cls(profile_fingerprint())

    def require_intent(self, intent: CommandIntent) -> None:
        if (
            intent.backend != "windows-appcontainer-v1"
            or intent.distribution != WINDOWS_LOCAL_DISTRIBUTION
            or intent.profile_sha256 != self.profile_sha256
            or profile_fingerprint() != self.profile_sha256
        ):
            raise PermissionError(
                "Execution grant does not match the Windows AppContainer profile."
            )

    async def run(
        self,
        intent: CommandIntent,
        snapshot: WorkspaceSnapshot,
        *,
        deadline: float,
        on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
        on_protocol_frame: Callable[[bytes], Awaitable[bytes | None]] | None = None,
        on_workspace_result: Callable[[Path], Awaitable[None]] | None = None,
        isolated_python_worker: bool = False,
        max_seconds: int | None = None,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
        max_workspace_bytes: int = MAX_PRIVATE_WORKSPACE_BYTES,
        streaming_protocol: bool = False,
        max_protocol_bytes: int = MAX_OUTPUT_BYTES,
    ) -> dict[str, Any]:
        self.require_intent(intent)
        duration = intent.max_seconds if max_seconds is None else max_seconds
        if type(duration) is not int or not 1 <= duration <= 120:
            raise ValueError("Windows isolated command runtime must be 1..120 seconds.")
        if (
            type(max_output_bytes) is not int
            or not MAX_OUTPUT_BYTES <= max_output_bytes <= 8 * 1024 * 1024
        ):
            raise ValueError("Windows isolated command output limit must be 1..8 MiB.")
        if (
            type(max_workspace_bytes) is not int
            or not MAX_PRIVATE_WORKSPACE_BYTES <= max_workspace_bytes <= MAX_PRIVATE_COMMAND_BYTES
        ):
            raise ValueError("Windows isolated workspace limit is outside its supported bound.")
        if streaming_protocol and on_protocol_frame is None:
            raise ValueError("Streaming protocol mode requires an interactive frame handler.")
        if type(max_protocol_bytes) is not int or not 1 <= max_protocol_bytes <= 512 * 1024 * 1024:
            raise ValueError("Windows protocol stream limit must be 1..512 MiB.")
        if deadline - time.monotonic() < duration:
            raise TimeoutError("Command duration exceeds the remaining parent time budget.")
        executable = _resolve_executable(intent.argv[0])
        if isolated_python_worker and (
            executable != Path(sys.executable).resolve(strict=True) or getattr(sys, "frozen", False)
        ):
            raise PermissionError(
                "Source Python worker access is restricted to this engine runtime."
            )
        try:
            selected_workspace: Path | None = Path(intent.workspace).resolve(strict=True)
        except OSError:
            selected_workspace = None
        executable_in_workspace = selected_workspace is not None and executable.is_relative_to(
            selected_workspace
        )
        staging_root = _private_root()
        run_root = Path(tempfile.mkdtemp(prefix=f"{intent.execution_id}-", dir=staging_root))
        shim_root: Path | None = None
        workspace = run_root / "workspace"
        try:
            materialize_snapshot(snapshot, workspace)
            if executable_in_workspace:
                assert selected_workspace is not None
                relative = executable.relative_to(selected_workspace).as_posix().casefold()
                staged = next(
                    (entry for entry in snapshot.files if entry.path.casefold() == relative),
                    None,
                )
                if staged is None:
                    raise PermissionError(
                        "Commands must use executables present in the private snapshot or an installed Windows runtime."
                    )
                executable = workspace.joinpath(*staged.path.split("/"))
            temporary = run_root / "child-temp"
            temporary.mkdir()
            shim_root = Path(
                tempfile.mkdtemp(prefix=f"{intent.execution_id}-shim-", dir=staging_root)
            )
            shim_temporary = shim_root / "temp"
            shim_temporary.mkdir()
        except BaseException:
            shutil.rmtree(run_root, ignore_errors=True)
            if shim_root is not None:
                shutil.rmtree(shim_root, ignore_errors=True)
            raise
        access: AppContainerAccess | None = None
        process: asyncio.subprocess.Process | None = None
        stdout = bytearray()
        stderr = bytearray()
        streamed = 0
        protocol_streamed = 0
        stdout_bytes = 0
        stdout_digest = hashlib.sha256()
        started = time.monotonic()
        timed_out = False
        reader_tasks: list[asyncio.Task[None]] = []
        process_wait: asyncio.Task[int] | None = None
        workspace_monitor: asyncio.Task[None] | None = None
        try:
            access = AppContainerAccess(
                str(executable),
                intent.argv[1:],
                str(workspace),
                ([str(executable.parent)] if _trusted_executable_directory(executable) else [])
                + (
                    [
                        str(Path(__file__).resolve().parents[2]),
                        str(Path(sys.prefix) / "Lib" / "site-packages"),
                        str(Path(sys.base_prefix)),
                    ]
                    if isolated_python_worker
                    else []
                ),
                [str(workspace), str(temporary)],
                _journal_root(),
                grant_argument_paths=False,
                grant_executable_directory=(
                    executable_in_workspace or _trusted_executable_directory(executable)
                ),
            )
            access.grant_existing_tree(
                str(workspace), FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE
            )
            env = _restricted_environment(executable, shim_temporary)
            child_environment = _restricted_environment(executable, temporary)
            # Windows appends Packages\\<container>\\AC to the inherited base
            # local-data path when it builds an AppContainer environment. Use
            # the matching base from GetAppContainerFolderPath so this works
            # even when the engine's own LOCALAPPDATA is redirected for tests.
            child_environment["LOCALAPPDATA"] = str(access.private_temp_path.parent.parents[2])
            shim_args = (
                ["--mcp-appcontainer-shim"]
                if getattr(sys, "frozen", False)
                else ["-m", "xaa.runtime.mcp_appcontainer", "--shim"]
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                *shim_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
                env=env,
                creationflags=0x08000000,
            )
            owner_running, owner_creation_time = process_identity(os.getpid())
            if not owner_running or owner_creation_time is None:
                raise RuntimeError("Windows could not identify the command isolation owner.")
            bootstrap = {
                "command": str(executable),
                "args": intent.argv[1:],
                "cwd": str(workspace),
                "profile_name": access.profile_name,
                "owner_pid": os.getpid(),
                "owner_creation_time": owner_creation_time,
                "max_seconds": duration,
                "command_limits": True,
                "child_environment": child_environment,
            }
            assert process.stdin is not None
            process.stdin.write(
                BOOTSTRAP_PREFIX + json.dumps(bootstrap, separators=(",", ":")).encode() + b"\n"
            )
            await process.stdin.drain()
            if on_protocol_frame is None:
                process.stdin.close()
            self._active[intent.execution_id] = process

            assert process.stdout is not None
            marker = await process.stdout.readexactly(4)
            if marker == READY_FRAME[:4]:
                remainder = await process.stdout.readexactly(len(READY_FRAME) - 4)
                if marker + remainder != READY_FRAME:
                    raise RuntimeError(
                        "Windows command isolation returned an invalid launch frame."
                    )
            elif marker == ERROR_FRAME_PREFIX:
                size_header = await process.stdout.readexactly(2)
                frame_size = int.from_bytes(size_header, "big")
                if frame_size > 32:
                    raise RuntimeError(
                        "Windows command isolation returned an invalid launch frame."
                    )
                frame = marker + size_header + await process.stdout.readexactly(frame_size)
                raise RuntimeError(startup_error_message(frame))
            else:
                raise RuntimeError("Windows command isolation did not confirm the sandbox launch.")

            async def read_stream(
                name: Literal["stdout", "stderr"], reader: asyncio.StreamReader
            ) -> None:
                nonlocal streamed, protocol_streamed, stdout_bytes
                target = stdout if name == "stdout" else stderr
                protocol_buffer = bytearray()
                while chunk := await reader.read(16384):
                    if name == "stdout" and on_protocol_frame is not None:
                        stdout_bytes += len(chunk)
                        stdout_digest.update(chunk)
                        if streaming_protocol:
                            protocol_streamed += len(chunk)
                            if protocol_streamed > max_protocol_bytes:
                                raise ValueError(
                                    "Windows command protocol stream exceeded its limit."
                                )
                        else:
                            streamed += len(chunk)
                            target.extend(chunk)
                            if streamed > max_output_bytes:
                                if max_output_bytes == MAX_OUTPUT_BYTES:
                                    raise ValueError(
                                        "Windows command output exceeded the 1 MiB limit."
                                    )
                                raise ValueError(
                                    "Windows command output exceeded its configured limit."
                                )
                        protocol_buffer.extend(chunk)
                        while b"\n" in protocol_buffer:
                            frame, _, remainder = protocol_buffer.partition(b"\n")
                            protocol_buffer[:] = remainder
                            if not frame or len(frame) > MAX_INTERACTIVE_FRAME_BYTES:
                                raise ValueError(
                                    "Windows command emitted an invalid protocol frame."
                                )
                            response = await on_protocol_frame(bytes(frame))
                            if response is None:
                                if process.stdin is not None and not process.stdin.is_closing():
                                    process.stdin.close()
                                continue
                            if (
                                len(response) > MAX_INTERACTIVE_FRAME_BYTES
                                or not response.endswith(b"\n")
                                or process.stdin is None
                                or process.stdin.is_closing()
                            ):
                                raise ValueError(
                                    "Windows command protocol response exceeded its limit."
                                )
                            process.stdin.write(response)
                            await process.stdin.drain()
                        if len(protocol_buffer) > MAX_INTERACTIVE_FRAME_BYTES:
                            raise ValueError("Windows command protocol frame exceeded its limit.")
                    elif name == "stdout" and protocol_buffer:
                        raise RuntimeError(
                            "Windows command protocol ended with an incomplete frame."
                        )
                    else:
                        streamed += len(chunk)
                        if name == "stdout":
                            stdout_bytes += len(chunk)
                            stdout_digest.update(chunk)
                        target.extend(chunk)
                        if streamed > max_output_bytes:
                            if max_output_bytes == MAX_OUTPUT_BYTES:
                                raise ValueError("Windows command output exceeded the 1 MiB limit.")
                            raise ValueError(
                                "Windows command output exceeded its configured limit."
                            )
                    if on_output is not None:
                        await on_output(name, chunk)
                if name == "stdout" and protocol_buffer:
                    raise RuntimeError("Windows command protocol ended with an incomplete frame.")

            assert process.stderr is not None
            reader_tasks = [
                asyncio.create_task(read_stream("stdout", process.stdout)),
                asyncio.create_task(read_stream("stderr", process.stderr)),
            ]

            async def monitor_workspace() -> None:
                assert process_wait is not None
                while not process_wait.done():
                    _private_workspace_size(workspace, max_bytes=max_workspace_bytes)
                    staging_bytes = _private_workspace_size(
                        run_root, max_bytes=MAX_PRIVATE_COMMAND_BYTES
                    )
                    _private_workspace_size(
                        access.private_temp_path,
                        max_bytes=MAX_PRIVATE_COMMAND_BYTES - staging_bytes,
                    )
                    assert shim_root is not None
                    _private_workspace_size(shim_root, max_bytes=MAX_SHIM_RUNTIME_BYTES)
                    await asyncio.sleep(0.1)

            process_wait = asyncio.create_task(process.wait())
            workspace_monitor = asyncio.create_task(monitor_workspace())
            try:
                async with asyncio.timeout(min(deadline - time.monotonic(), duration + 3)):
                    await asyncio.gather(*reader_tasks, workspace_monitor)
                    exit_code = await process_wait
            except TimeoutError:
                timed_out = True
                await stop_owned_process(process)
                await asyncio.gather(*reader_tasks, return_exceptions=True)
                exit_code = await process_wait
            if timed_out:
                termination: Literal["finished", "timeout"] = "timeout"
                output_complete = False
                normal_exit: int | None = None
            else:
                termination = "timeout" if exit_code == 124 else "finished"
                output_complete = exit_code not in {124, 125}
                normal_exit = exit_code if output_complete else None
            if on_workspace_result is not None and output_complete and exit_code == 0:
                await on_workspace_result(workspace)
            receipt: dict[str, Any] = {
                "execution_id": intent.execution_id,
                "snapshot_sha256": snapshot.sha256,
                "filter_sha256": self.profile_sha256,
                "termination_reason": termination
                if output_complete or timed_out or exit_code == 124
                else "signal",
                "exit_code": normal_exit,
                "supervisor_exit_code": exit_code,
                "succeeded": output_complete and exit_code == 0,
                "output_complete": output_complete,
                "cleanup_confirmed": True,
                "workspace_changes_persisted": False,
                "stdout_b64": __import__("base64").b64encode(stdout).decode("ascii"),
                "stderr_b64": __import__("base64").b64encode(stderr).decode("ascii"),
                "stdout_sha256": stdout_digest.hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": len(stderr),
                "elapsed_seconds": max(0.0, time.monotonic() - started),
            }
            self._receipts[intent.execution_id] = receipt
            return receipt
        except BaseException:
            if process is not None and process.returncode is None:
                await stop_owned_process(process)
            raise
        finally:
            for task in reader_tasks:
                if not task.done():
                    task.cancel()
            if reader_tasks:
                await asyncio.gather(*reader_tasks, return_exceptions=True)
            if workspace_monitor is not None:
                if not workspace_monitor.done():
                    workspace_monitor.cancel()
                await asyncio.gather(workspace_monitor, return_exceptions=True)
            self._active.pop(intent.execution_id, None)
            if access is not None:
                access.close()
            shutil.rmtree(run_root, ignore_errors=False)
            shutil.rmtree(shim_root, ignore_errors=False)

    async def control(
        self,
        action: Literal["stop", "cleanup", "status"],
        intent: CommandIntent,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        if time.monotonic() >= deadline:
            raise TimeoutError("Windows command control deadline expired.")
        self.require_intent(intent)
        if action not in {"stop", "cleanup", "status"}:
            raise ValueError("Unsupported Windows command control action.")
        process = self._active.get(intent.execution_id)
        if action == "stop" and process is not None and process.returncode is None:
            await stop_owned_process(process)
        receipt = self._receipts.get(intent.execution_id)
        return {
            "execution_id": intent.execution_id,
            "closed": process is None or process.returncode is not None,
            "cleanup_confirmed": process is None or process.returncode is not None,
            "manager_alive": process is not None and process.returncode is None,
            "outcome": {"state": "finished", "result": receipt} if receipt is not None else None,
        }

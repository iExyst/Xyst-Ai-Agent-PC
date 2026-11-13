"""Bounded stdio transport for the pinned MCP Python SDK.

The SDK's stdio reader buffers each newline-delimited JSON-RPC message without
a size limit. This adapter preserves the SDK's process and shutdown handling
while rejecting oversized frames before JSON parsing.
"""

# pyright: reportPrivateUsage=false
# This adapter intentionally reuses SDK internals to preserve process-tree cleanup.

from __future__ import annotations

import json
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TextIO, cast

import anyio
import anyio.lowlevel
from anyio.streams.text import TextReceiveStream
from mcp.client._transport import TransportStreams
from mcp.client.stdio import (
    _WRITER_FLUSH_TIMEOUT,
    StdioServerParameters,
    _aclose_all,
    _create_platform_compatible_process,
    _drain_stdout,
    _get_executable_command,
    _parse_line,
    _stop_server_process,
    get_default_environment,
)
from mcp.os.win32.utilities import ServerProcess, _process_jobs
from mcp.shared.message import SessionMessage

from xaa.runtime.mcp_appcontainer import (
    BOOTSTRAP_PREFIX,
    ERROR_FRAME_PREFIX,
    MAX_STARTUP_ERROR_FRAME_BYTES,
    READY_FRAME,
    AppContainerAccess,
    startup_error_message,
    startup_os_error_code,
)

logger = logging.getLogger(__name__)

# MCP messages are JSON-RPC lines. 1 MiB accommodates large tool schemas and
# resource listings while bounding memory before the session/result limits run.
MCP_MAX_STDIO_FRAME_BYTES = 1024 * 1024
MCP_APPCONTAINER_STARTUP_TIMEOUT = 60


class MCPStdioFrameLimitError(ValueError):
    """An MCP server emitted a stdio frame larger than the configured cap."""


class MCPStdioResourceLimitError(RuntimeError):
    """A configured stdio process limit could not be enforced."""


async def _apply_windows_process_limits(
    process: ServerProcess,
    memory_limit_mb: int | None,
    cpu_limit_percent: int | None,
) -> None:
    if memory_limit_mb is None and cpu_limit_percent is None:
        return
    if memory_limit_mb is not None and not 128 <= memory_limit_mb <= 65536:
        raise ValueError("MCP stdio memory limits must be between 128 and 65,536 MiB.")
    if cpu_limit_percent is not None and not 1 <= cpu_limit_percent <= 100:
        raise ValueError("MCP stdio CPU limits must be between 1 and 100 percent.")
    if sys.platform != "win32":
        raise MCPStdioResourceLimitError(
            "Configured MCP process memory limits are currently supported on Windows only."
        )

    # The pinned SDK owns process creation and Job Object lifetime. Configure its
    # already-assigned Job Object so its kill-on-close semantics remain intact.
    import win32job  # type: ignore[import-not-found]

    job_api: Any = win32job

    job = cast(Any, _process_jobs.get(process))
    if job is None:
        raise MCPStdioResourceLimitError(
            "Windows could not assign this MCP server to a resource-control Job Object."
        )
    try:
        if memory_limit_mb is not None:
            extended_info: Any = job_api.QueryInformationJobObject(
                job, job_api.JobObjectExtendedLimitInformation
            )
            extended_info["BasicLimitInformation"]["LimitFlags"] |= (
                job_api.JOB_OBJECT_LIMIT_JOB_MEMORY
            )
            extended_info["JobMemoryLimit"] = memory_limit_mb * 1024 * 1024
            job_api.SetInformationJobObject(
                job, job_api.JobObjectExtendedLimitInformation, extended_info
            )
        if cpu_limit_percent is not None:
            import ctypes

            class CpuRateControlInformation(ctypes.Structure):
                _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]

            # JobObjectCpuRateControlInformation = 15,
            # ENABLE | HARD_CAP = 0x1 | 0x4; rate units are 1/100 of one percent.
            cpu_info = CpuRateControlInformation(0x1 | 0x4, cpu_limit_percent * 100)
            set_information: Any = ctypes.WinDLL(
                "kernel32", use_last_error=True
            ).SetInformationJobObject
            set_information.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            set_information.restype = ctypes.c_int
            succeeded = set_information(
                ctypes.c_void_p(int(job)),
                15,
                ctypes.byref(cpu_info),
                ctypes.sizeof(cpu_info),
            )
            if not succeeded:
                raise ctypes.WinError(ctypes.get_last_error())
    except Exception as error:
        raise MCPStdioResourceLimitError(
            "Windows could not enforce the configured MCP server resource limits."
        ) from error


@asynccontextmanager
async def bounded_stdio_client(
    server: StdioServerParameters,
    *,
    max_frame_bytes: int = MCP_MAX_STDIO_FRAME_BYTES,
    memory_limit_mb: int | None = None,
    cpu_limit_percent: int | None = None,
    network_isolation: bool = False,
    isolation_read_paths: tuple[str, ...] = (),
    isolation_write_paths: tuple[str, ...] = (),
    isolation_journal_directory: str | None = None,
    errlog: TextIO = sys.stderr,
) -> AsyncGenerator[TransportStreams]:
    """Spawn an MCP stdio server and reject any inbound frame over the limit.

    The pinned SDK's private process helpers are used deliberately so its
    Windows Job Object and POSIX process-group cleanup semantics are retained.
    """
    if max_frame_bytes <= 0:
        raise ValueError("max_frame_bytes must be positive")
    if memory_limit_mb is not None and not 128 <= memory_limit_mb <= 65536:
        raise ValueError("MCP stdio memory limits must be between 128 and 65,536 MiB.")
    if cpu_limit_percent is not None and not 1 <= cpu_limit_percent <= 100:
        raise ValueError("MCP stdio CPU limits must be between 1 and 100 percent.")

    command = await _get_executable_command(server.command)
    environment = get_default_environment() | (server.env or {})
    appcontainer_access: Any | None = None
    process: ServerProcess | None = None
    try:
        launch_command = command
        launch_args = server.args
        if network_isolation:
            if sys.platform != "win32":
                raise MCPStdioResourceLimitError(
                    "MCP network isolation is currently supported on Windows only."
                )
            appcontainer_access = AppContainerAccess(
                command,
                server.args,
                str(server.cwd) if server.cwd is not None else None,
                list(isolation_read_paths),
                list(isolation_write_paths),
                isolation_journal_directory or str(Path.home() / ".xaa" / "mcp-appcontainer"),
                # Python MCP servers load their stdlib, virtualenv packages,
                # native extension DLLs, and the selected interpreter runtime.
                # Grant that configured environment for read/execute inside
                # the container; other executables receive only their file.
                grant_executable_directory=(
                    Path(command).name.casefold() in {"python.exe", "pythonw.exe"}
                    and Path(command).parent.name.casefold() == "scripts"
                ),
                prepare_python_environment=True,
            )
            launch_command = sys.executable
            launch_args = (
                ["--mcp-appcontainer-shim"]
                if getattr(sys, "frozen", False)
                else ["-m", "xaa.runtime.mcp_appcontainer", "--shim"]
            )
        process = await _create_platform_compatible_process(
            command=launch_command,
            args=launch_args,
            env=environment,
            errlog=errlog,
            cwd=server.cwd,
        )
        await _apply_windows_process_limits(process, memory_limit_mb, cpu_limit_percent)
        if appcontainer_access is not None:
            assert process.stdin is not None and process.stdout is not None
            stdout = process.stdout
            child_environment = environment | {
                # Frozen one-file MCP servers (including the packaged engine's
                # own shim) extract their runtime beneath TEMP. The host's temp
                # directory is outside the AppContainer, so direct extraction
                # there fails with Access Denied during Python DLL loading.
                "TEMP": str(appcontainer_access.private_temp_path),
                "TMP": str(appcontainer_access.private_temp_path),
                # Let Windows resolve the matching Packages\\<profile>\\AC
                # location instead of inheriting the host user's data root.
                "LOCALAPPDATA": str(appcontainer_access.private_temp_path.parent.parents[2]),
            }
            bootstrap = {
                "command": appcontainer_access.command,
                "args": server.args,
                "cwd": server.cwd,
                "profile_name": appcontainer_access.profile_name,
                "child_environment": child_environment,
            }
            await process.stdin.send(
                BOOTSTRAP_PREFIX + json.dumps(bootstrap, separators=(",", ":")).encode() + b"\n"
            )

            async def receive_exact(size: int) -> bytes:
                received = bytearray()
                while len(received) < size:
                    received.extend(await stdout.receive(size - len(received)))
                return bytes(received)

            with anyio.fail_after(MCP_APPCONTAINER_STARTUP_TIMEOUT):
                prefix = await receive_exact(len(ERROR_FRAME_PREFIX))
                if prefix == ERROR_FRAME_PREFIX:
                    size = int.from_bytes(await receive_exact(2), "big")
                    if size > MAX_STARTUP_ERROR_FRAME_BYTES:
                        raise MCPStdioResourceLimitError(
                            "Windows could not start the network-isolated MCP server."
                        )
                    error_frame = prefix + size.to_bytes(2, "big") + await receive_exact(size)
                    raise MCPStdioResourceLimitError(startup_error_message(error_frame))
                ready = prefix + await receive_exact(len(READY_FRAME) - len(prefix))
            if ready != READY_FRAME:
                raise MCPStdioResourceLimitError(
                    "Windows could not start the network-isolated MCP server."
                )
    except BaseException as error:
        try:
            with anyio.CancelScope(shield=True):
                if process is not None:
                    await _stop_server_process(process)
        finally:
            if appcontainer_access is not None:
                appcontainer_access.close()
        if (
            network_isolation
            and isinstance(error, Exception)
            and not isinstance(error, MCPStdioResourceLimitError)
        ):
            message = "Windows could not enforce network isolation for this MCP server."
            code = startup_os_error_code(error)
            if code is not None:
                message += f" (Windows error {code})."
            raise MCPStdioResourceLimitError(message) from error
        raise
    assert process is not None

    read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](
        0
    )
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)
    shutting_down = False
    writer_done = anyio.Event()

    async def stdout_reader() -> None:
        assert process.stdout, "Opened process is missing stdout"
        stdout = TextReceiveStream(
            process.stdout,
            encoding=server.encoding,
            errors=server.encoding_error_handler,
        )
        try:
            async with read_stream_writer:
                try:
                    buffer = ""
                    buffer_bytes = 0
                    async for chunk in stdout:
                        remainder = chunk
                        while remainder:
                            prefix, separator, remainder = remainder.partition("\n")
                            prefix_bytes = len(
                                prefix.encode(
                                    server.encoding,
                                    errors=server.encoding_error_handler,
                                )
                            )
                            frame_bytes = buffer_bytes + prefix_bytes
                            if frame_bytes > max_frame_bytes:
                                raise MCPStdioFrameLimitError(
                                    "MCP server stdout frame exceeded the "
                                    f"{max_frame_bytes}-byte limit."
                                )
                            if separator:
                                await read_stream_writer.send(_parse_line(buffer + prefix))
                                buffer = ""
                                buffer_bytes = 0
                            else:
                                buffer += prefix
                                buffer_bytes = frame_bytes
                finally:
                    await _drain_stdout(process)
        except anyio.ClosedResourceError:
            pass
        except (anyio.BrokenResourceError, ConnectionError):
            if not shutting_down:
                logger.exception("Reading from the MCP server's stdout failed mid-session")

    async def stdin_writer() -> None:
        assert process.stdin, "Opened process is missing stdin"
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    payload = session_message.message.model_dump_json(
                        by_alias=True, exclude_unset=True
                    )
                    data = (payload + "\n").encode(
                        encoding=server.encoding,
                        errors=server.encoding_error_handler,
                    )
                    await process.stdin.send(data)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
            await read_stream_writer.aclose()
        finally:
            writer_done.set()

    async def shutdown() -> None:
        read_stream.close()
        write_stream.close()
        with anyio.move_on_after(_WRITER_FLUSH_TIMEOUT) as flush_scope:
            await writer_done.wait()
        if flush_scope.cancelled_caught:
            await anyio.lowlevel.cancel_shielded_checkpoint()
        await _stop_server_process(process)
        await _aclose_all(read_stream, write_stream, read_stream_writer, write_stream_reader)
        await anyio.lowlevel.checkpoint()

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(stdout_reader)
            task_group.start_soon(stdin_writer)
            try:
                yield read_stream, write_stream
            finally:
                shutting_down = True
                with anyio.CancelScope(shield=True):
                    await shutdown()
                task_group.cancel_scope.cancel()
        await anyio.lowlevel.cancel_shielded_checkpoint()
    finally:
        if appcontainer_access is not None:
            appcontainer_access.close()

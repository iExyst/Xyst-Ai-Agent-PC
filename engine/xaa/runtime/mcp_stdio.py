"""Bounded stdio transport for the pinned MCP Python SDK.

The SDK's stdio reader buffers each newline-delimited JSON-RPC message without
a size limit. This adapter preserves the SDK's process and shutdown handling
while rejecting oversized frames before JSON parsing.
"""

# pyright: reportPrivateUsage=false
# This adapter intentionally reuses SDK internals to preserve process-tree cleanup.

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TextIO

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
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)

# MCP messages are JSON-RPC lines. 1 MiB accommodates large tool schemas and
# resource listings while bounding memory before the session/result limits run.
MCP_MAX_STDIO_FRAME_BYTES = 1024 * 1024


class MCPStdioFrameLimitError(ValueError):
    """An MCP server emitted a stdio frame larger than the configured cap."""


@asynccontextmanager
async def bounded_stdio_client(
    server: StdioServerParameters,
    *,
    max_frame_bytes: int = MCP_MAX_STDIO_FRAME_BYTES,
    errlog: TextIO = sys.stderr,
) -> AsyncGenerator[TransportStreams]:
    """Spawn an MCP stdio server and reject any inbound frame over the limit.

    The pinned SDK's private process helpers are used deliberately so its
    Windows Job Object and POSIX process-group cleanup semantics are retained.
    """
    if max_frame_bytes <= 0:
        raise ValueError("max_frame_bytes must be positive")

    command = await _get_executable_command(server.command)
    process = await _create_platform_compatible_process(
        command=command,
        args=server.args,
        env=get_default_environment() | (server.env or {}),
        errlog=errlog,
        cwd=server.cwd,
    )

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

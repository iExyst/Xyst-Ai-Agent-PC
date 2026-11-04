"""Bounded binary IPC with an awaited, application-owned child process."""

import asyncio
import os
from collections.abc import Sequence

from xaa.runtime.processes import stop_owned_process


class TransportError(RuntimeError):
    """No authoritative reply; side effects may require reconciliation."""


async def request_process(
    command: Sequence[str], payload: bytes, *, timeout: float, output_limit: int
) -> bytes:
    if timeout <= 0:
        raise TimeoutError("Transport deadline expired before launch.")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            key: value
            for key, value in os.environ.items()
            if key.upper() not in {"XAA_API_TOKEN", "LM_STUDIO_API_TOKEN", "LM_API_TOKEN"}
        },
        creationflags=0x08000000 if os.name == "nt" else 0,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None

    async def write() -> bytes:
        assert process.stdin is not None
        try:
            process.stdin.write(payload)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
        return b""

    async def read(stream: asyncio.StreamReader, limit: int) -> bytes:
        output = bytearray()
        while chunk := await stream.read(16384):
            output.extend(chunk)
            if len(output) > limit:
                raise TransportError("Owned transport exceeded its output limit.")
        return bytes(output)

    tasks = [
        asyncio.create_task(write()),
        asyncio.create_task(read(process.stdout, output_limit)),
        asyncio.create_task(read(process.stderr, 16384)),
    ]
    try:
        async with asyncio.timeout(timeout):
            _, output, _ = await asyncio.gather(*tasks)
            if await process.wait() != 0:
                raise TransportError("Owned transport failed; reconcile any reserved execution.")
            assert isinstance(output, bytes)
            return output
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        async def discard(stream: asyncio.StreamReader) -> None:
            while await stream.read(16384):
                pass

        # Drain without retaining bytes while terminating, including a flooded pipe.
        await asyncio.gather(
            stop_owned_process(process), discard(process.stdout), discard(process.stderr)
        )

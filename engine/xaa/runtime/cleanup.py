"""Keep ownership until cleanup ends, including repeated caller cancellation."""

import asyncio
from collections.abc import Awaitable


async def finish_cleanup[T](operation: Awaitable[T]) -> T:
    """Defer new cancellations; still observe cleanup failure and restore cancellation."""
    owned = asyncio.ensure_future(operation)
    interrupted = False
    while not owned.done():
        try:
            await asyncio.shield(owned)
        except asyncio.CancelledError:
            interrupted = True
    result = owned.result()
    if interrupted:
        raise asyncio.CancelledError
    return result

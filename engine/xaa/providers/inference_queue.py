"""Bounded FIFO admission for local inference and model lifecycle work."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager


class InferenceQueueError(RuntimeError):
    """An inference request could not be admitted by the local queue."""


class InferenceQueueFull(InferenceQueueError):
    """The pending request or estimated working-set limit was reached."""


class InferenceQueueTimeout(InferenceQueueError):
    """A request exceeded its queue-wait deadline before reaching LM Studio."""


class InferenceQueue:
    """Serialize one local server while bounding queued count and estimated weight.

    The weight combines a conservative UTF-8 byte estimate of prompt and tool JSON
    with the requested output allowance. It is not a token count or VRAM estimate.
    Context safety is independently enforced by the provider's context admission path.
    """

    def __init__(
        self,
        *,
        max_pending: int = 16,
        max_reserved_weight: int = 2_000_000,
        wait_timeout: float = 180.0,
    ) -> None:
        if max_pending < 1 or max_reserved_weight < 1 or wait_timeout <= 0:
            raise ValueError("Inference queue limits must be positive.")
        self.max_pending = max_pending
        self.max_reserved_weight = max_reserved_weight
        self.wait_timeout = wait_timeout
        self.lock = asyncio.Lock()
        self._waiting = 0
        self._reserved_weight = 0
        self._active = False

    @property
    def waiting(self) -> int:
        """Number of requests currently waiting for the serialized slot."""
        return self._waiting

    @property
    def reserved_weight(self) -> int:
        """Estimated queue weight across active and queued requests."""
        return self._reserved_weight

    @property
    def active(self) -> bool:
        """Whether one operation currently owns the serialized provider slot."""
        return self._active

    def snapshot(self) -> dict[str, int | bool]:
        """Expose bounded queue counters without request contents or model IDs."""
        return {
            "active": self._active,
            "waiting": self._waiting,
            "reserved_weight": self._reserved_weight,
            "max_reserved_weight": self.max_reserved_weight,
            "max_pending": self.max_pending,
        }

    @asynccontextmanager
    async def slot(self, weight: int, *, wait_timeout: float | None = None) -> AsyncGenerator[None]:
        if type(weight) is not int or weight < 0:
            raise ValueError("Inference queue weight must be a nonnegative integer.")
        if weight > self.max_reserved_weight:
            raise InferenceQueueFull(
                "This request exceeds the local inference queue's working-set limit. "
                "Reduce the prompt or output allowance, or increase the queue limit."
            )
        if self._waiting >= self.max_pending:
            raise InferenceQueueFull(
                "The local inference queue is full. Wait for active model work to finish."
            )
        if self._reserved_weight + weight > self.max_reserved_weight:
            raise InferenceQueueFull(
                "The local inference queue has no capacity for this request's estimated "
                "payload weight. Wait for queued work to finish."
            )

        self._waiting += 1
        self._reserved_weight += weight
        waiting = True
        acquired = False
        try:
            timeout = self.wait_timeout if wait_timeout is None else wait_timeout
            try:
                async with asyncio.timeout(timeout):
                    await self.lock.acquire()
            except TimeoutError:
                raise InferenceQueueTimeout(
                    "Timed out waiting for local inference capacity. Retry after current "
                    "model work finishes."
                ) from None
            acquired = True
            self._waiting -= 1
            waiting = False
            self._active = True
            yield
        finally:
            if acquired:
                self._active = False
                self.lock.release()
            if waiting:
                self._waiting -= 1
            self._reserved_weight -= weight

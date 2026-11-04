"""Startup cleanup of stopped private executions; never launches a command/model."""

import asyncio
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable

from xaa.runtime.command_controller import CommandController
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore

logger = logging.getLogger(__name__)


class StoppedCommandRecovery:
    def __init__(
        self, store: RunStore, discover: Callable[[str], Awaitable[WSLTransport]] | None = None
    ):
        self.store = store
        self.discover = discover or self._discover
        self.retry_after: dict[tuple[str, str, int], float] = {}

    @staticmethod
    async def _discover(distribution: str) -> WSLTransport:
        return await WSLTransport.discover(distribution, deadline=time.monotonic() + 15)

    async def run_once(self) -> int:
        journal = CommandJournal(self.store)
        candidates = journal.stopped_candidates()
        keys = {(item["run_id"], item["call_id"], item["revision"]) for item in candidates}
        self.retry_after = {key: value for key, value in self.retry_after.items() if key in keys}
        observed = 0
        for item in candidates:
            run_id, call_id, revision = item["run_id"], item["call_id"], item["revision"]
            key = (run_id, call_id, revision)
            if self.retry_after.get(key, 0) > time.monotonic():
                continue
            try:
                intent = journal.stopped_intent(run_id, revision, call_id)
                transport = await self.discover(intent.distribution)
                proof = await CommandController(self.store, transport).cleanup(intent)
                journal.record_stopped_cleanup(
                    run_id, revision, call_id, proof, preserve_available=True
                )
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                self.retry_after[key] = time.monotonic() + 60
                logger.warning(
                    "Command cleanup deferred for run %s (%s)", run_id, type(exc).__name__
                )
            else:
                self.retry_after.pop(key, None)
                observed += 1
        return observed

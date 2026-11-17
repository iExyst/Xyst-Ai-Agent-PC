"""Track live specialist coroutines so one assignment can be stopped independently."""

import asyncio

from xaa.runtime.cleanup import finish_cleanup


class SpecialistWorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[tuple[str, str], asyncio.Task[None]] = {}

    def register(self, run_id: str, task_id: str, worker: asyncio.Task[None]) -> None:
        key = (run_id, task_id)
        current = self._workers.get(key)
        if current is not None and not current.done():
            raise RuntimeError("A specialist worker is already registered for this task.")
        self._workers[key] = worker

    def unregister(self, run_id: str, task_id: str, worker: asyncio.Task[None]) -> None:
        key = (run_id, task_id)
        if self._workers.get(key) is worker:
            self._workers.pop(key, None)

    def is_active(self, run_id: str, task_id: str) -> bool:
        worker = self._workers.get((run_id, task_id))
        return worker is not None and not worker.done()

    async def cancel(self, run_id: str, task_id: str) -> bool:
        worker = self._workers.get((run_id, task_id))
        if worker is None or worker.done():
            return False
        worker.cancel()
        await finish_cleanup(asyncio.gather(worker, return_exceptions=True))
        return True

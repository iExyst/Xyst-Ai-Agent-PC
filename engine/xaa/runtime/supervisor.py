"""Own active coordinator tasks for one engine process."""

import asyncio
import logging

from xaa.runtime.cleanup import finish_cleanup
from xaa.runtime.coordinator import Coordinator

logger = logging.getLogger(__name__)


class RunSupervisor:
    def __init__(self, coordinator: Coordinator) -> None:
        self.coordinator = coordinator
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.recovery_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.recovery_task is None:
            self.recovery_task = asyncio.create_task(self._recover(), name="run-recovery")

    async def _recover(self) -> None:
        while True:
            for run_id in self.coordinator.store.recover_abandoned_runs():
                self.submit(run_id)
            await asyncio.sleep(2)

    def submit(self, run_id: str) -> None:
        active = self.tasks.get(run_id)
        if active is not None and not active.done():
            return
        task = asyncio.create_task(self.coordinator.execute(run_id), name=f"run:{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda finished: self._finished(run_id, finished))

    def submit_plan(self, run_id: str, token: str) -> bool:
        active = self.tasks.get(run_id)
        if active is not None and not active.done():
            return False
        task = asyncio.create_task(
            self.coordinator.continue_plan(run_id, token), name=f"plan:{run_id}"
        )
        self.tasks[run_id] = task
        task.add_done_callback(lambda finished: self._plan_finished(run_id, token, finished))
        return True

    def _plan_finished(self, run_id: str, token: str, task: asyncio.Task[None]) -> None:
        # Also release reserved ownership if cancellation happened before the coroutine started.
        run = self.coordinator.store.get_run(run_id)
        if (
            task.cancelled()
            and run is not None
            and run["state"] == "running"
            and run["lease_owner"] == token
        ):
            from xaa.runtime.states import RunState

            self.coordinator.store.transition(
                run_id, RunState.PAUSED, reason="worker_cancelled_before_start", owner=token
            )
        self.coordinator.store.release_lease(run_id, token)
        self._finished(run_id, task)

    def _finished(self, run_id: str, task: asyncio.Task[None]) -> None:
        if self.tasks.get(run_id) is task:
            self.tasks.pop(run_id, None)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error("Coordinator failed for run %s (%s)", run_id, type(error).__name__)

    async def stop(self, run_id: str) -> None:
        task = self.tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await finish_cleanup(asyncio.gather(task, return_exceptions=True))

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        if self.recovery_task is not None:
            tasks.append(self.recovery_task)
            self.recovery_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await finish_cleanup(asyncio.gather(*tasks, return_exceptions=True))

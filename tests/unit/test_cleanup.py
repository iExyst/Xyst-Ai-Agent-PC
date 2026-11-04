import asyncio

import pytest
from xaa.runtime.cleanup import finish_cleanup
from xaa.runtime.supervisor import RunSupervisor


async def test_repeated_cancellation_waits_for_owned_cleanup():
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def operation():
        started.set()
        await release.wait()
        finished.set()
        return 42

    task = asyncio.create_task(finish_cleanup(operation()))
    await started.wait()
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_cleanup_failure_is_observed_after_cancellation():
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation():
        started.set()
        await release.wait()
        raise RuntimeError("cleanup could not be confirmed")

    task = asyncio.create_task(finish_cleanup(operation()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(RuntimeError, match="confirmed"):
        await task


async def test_supervisor_close_cancels_all_owners_before_awaiting_cleanup():
    supervisor = RunSupervisor(None)
    started = [asyncio.Event(), asyncio.Event()]
    stopping = [asyncio.Event(), asyncio.Event()]
    finished = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def worker(index):
        started[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            stopping[index].set()
            await release.wait()
            finished[index].set()

    supervisor.recovery_task = asyncio.create_task(worker(0))
    supervisor.tasks["run"] = asyncio.create_task(worker(1))
    await asyncio.gather(*(event.wait() for event in started))
    close = asyncio.create_task(supervisor.close())
    await asyncio.gather(*(event.wait() for event in stopping))
    for _ in range(3):
        close.cancel()
        await asyncio.sleep(0)
    assert not close.done() and not any(event.is_set() for event in finished)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    assert all(event.is_set() for event in finished)

import asyncio
import sys

import pytest
from xaa.runtime import owned_transport
from xaa.runtime.owned_transport import TransportError, request_process


async def test_binary_transport_and_secret_environment_removal(monkeypatch):
    monkeypatch.setenv("XAA_API_TOKEN", "private-fixture")
    result = await request_process(
        [
            sys.executable,
            "-I",
            "-c",
            "import os,sys;assert 'XAA_API_TOKEN' not in os.environ;sys.stdout.buffer.write(sys.stdin.buffer.read())",
        ],
        b"\x00\xffbinary",
        timeout=5,
        output_limit=1024,
    )
    assert result == b"\x00\xffbinary"


@pytest.mark.parametrize("failure", ["stdout", "stderr", "timeout", "cancel"])
async def test_owned_child_is_awaited_after_failure(monkeypatch, failure):
    original = asyncio.create_subprocess_exec
    children = []

    async def track(*args, **kwargs):
        process = await original(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", track)
    code = "import time;time.sleep(60)"
    if failure in {"stdout", "stderr"}:
        code = f"import sys;sys.{failure}.buffer.write(b'x'*1000000);sys.{failure}.flush()"
    task = asyncio.create_task(
        request_process(
            [sys.executable, "-I", "-c", code],
            b"",
            timeout=0.3 if failure == "timeout" else 5,
            output_limit=1024,
        )
    )
    if failure == "cancel":
        async with asyncio.timeout(5):
            while not children:
                await asyncio.sleep(0.01)
        task.cancel()
    expected = (
        asyncio.CancelledError
        if failure == "cancel"
        else (TimeoutError if failure == "timeout" else TransportError)
    )
    with pytest.raises(expected):
        await task
    assert children[0].returncode is not None


async def test_expired_deadline_does_not_spawn(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Expired transport must not launch")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(TimeoutError):
        await request_process(["unused"], b"", timeout=0, output_limit=1)


async def test_repeated_cancel_cannot_abandon_actual_child_shutdown(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    child = []
    original = owned_transport.stop_owned_process

    async def controlled_shutdown(process):
        child.append(process)
        entered.set()
        await release.wait()
        await original(process)

    monkeypatch.setattr(owned_transport, "stop_owned_process", controlled_shutdown)
    task = asyncio.create_task(
        request_process(
            [sys.executable, "-I", "-c", "import time;time.sleep(60)"],
            b"",
            timeout=0.1,
            output_limit=1024,
        )
    )
    await entered.wait()
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child[0].returncode is not None

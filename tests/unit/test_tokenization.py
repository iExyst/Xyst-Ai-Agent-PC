import asyncio
import json
import time
from http import HTTPStatus

import pytest
from pydantic import SecretStr, ValidationError
from websockets.asyncio.server import serve
from xaa.providers.tokenization import PromptCountError, PromptCountRequest, count_prompt


def greeting(connection, request):
    if request.path == "/lmstudio-greeting":
        return connection.respond(HTTPStatus.OK, '{"lmstudio":true}')
    return None


def request():
    return PromptCountRequest.model_validate(
        {"model": "fixture", "messages": [{"role": "user", "content": "PRIVATE_FIXTURE_TEXT"}]}
    )


@pytest.mark.parametrize("credential", [None, "sk-lm-" + "A" * 8 + ":" + "B" * 20])
async def test_real_sdk_auth_and_cancellation_close_stalled_connection(monkeypatch, credential):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("LM_API_TOKEN", "sk-lm-" + "C" * 8 + ":" + "D" * 20)
    arrived = asyncio.Event()
    closed = asyncio.Event()
    frames = []

    async def stall(connection):
        frames.append(json.loads(await connection.recv()))
        arrived.set()
        await connection.wait_closed()
        closed.set()

    async with serve(stall, "127.0.0.1", 0, process_request=greeting) as server:
        port = server.sockets[0].getsockname()[1]
        task = asyncio.create_task(
            count_prompt(
                f"http://127.0.0.1:{port}",
                request(),
                SecretStr(credential) if credential else None,
                timeout=10,
            )
        )
        try:
            await asyncio.wait_for(arrived.wait(), 2)
            if credential:
                assert frames[0]["clientIdentifier"] == "A" * 8
                assert frames[0]["clientPasskey"] == "B" * 20
            else:
                assert frames[0]["clientIdentifier"].startswith("guest:")
                assert frames[0]["clientPasskey"] != "D" * 20
            started = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            await asyncio.wait_for(closed.wait(), 2)
            assert time.monotonic() - started < 2
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_real_sdk_timeout_redacts_input_and_closes_connection():
    closed = asyncio.Event()

    async def stall(connection):
        await connection.recv()
        await connection.wait_closed()
        closed.set()

    async with serve(stall, "127.0.0.1", 0, process_request=greeting) as server:
        port = server.sockets[0].getsockname()[1]
        started = time.monotonic()
        with pytest.raises(PromptCountError, match="timed out") as error:
            await count_prompt(f"http://127.0.0.1:{port}", request(), None, timeout=3)
        await asyncio.wait_for(closed.wait(), 2)
        assert time.monotonic() - started < 5
        assert "PRIVATE_FIXTURE_TEXT" not in str(error.value)


@pytest.mark.parametrize(
    "address",
    [
        "https://127.0.0.1:1234",
        "http://example.com:1234",
        "http://127.0.0.1:1234/proxy",
        "http://user:secret@127.0.0.1:1234",
    ],
)
async def test_counter_rejects_unsupported_destination_before_connection(address):
    with pytest.raises(PromptCountError, match="loopback"):
        await count_prompt(address, request(), None)


async def test_counter_rejects_oversized_request_before_connection():
    body = {
        "model": "fixture",
        "messages": [{"role": "user", "content": "x" * 65536} for _ in range(16)],
    }
    with pytest.raises(PromptCountError, match="one MiB"):
        await count_prompt("http://127.0.0.1:1", PromptCountRequest.model_validate(body), None)


async def test_counter_does_not_echo_malformed_tool_arguments():
    body = {
        "model": "fixture",
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "PRIVATE_BAD_JSON"},
                    }
                ],
            }
        ],
    }
    with pytest.raises(PromptCountError) as error:
        await count_prompt("http://127.0.0.1:1", PromptCountRequest.model_validate(body), None)
    assert "PRIVATE_BAD_JSON" not in str(error.value)


def test_counter_rejects_incomplete_tool_result():
    with pytest.raises(ValidationError):
        PromptCountRequest.model_validate(
            {"model": "fixture", "messages": [{"role": "tool", "content": "result"}]}
        )


async def test_count_queue_deadline_does_not_launch_worker(monkeypatch):
    from xaa.providers.lm_studio import LMStudioProvider

    provider = LMStudioProvider()
    calls = []

    async def unexpected_spawn(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Queued counter must not spawn a worker.")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_spawn)
    await provider._inference_lock.acquire()
    try:
        with pytest.raises(PromptCountError, match="waiting for local inference"):
            await provider.count_prompt(request(), timeout=0.05)
        assert calls == []
    finally:
        provider._inference_lock.release()
        await provider.close()


@pytest.mark.parametrize("mode", ["malformed", "oversized", "nonzero", "sdk_error"])
async def test_counter_rejects_broken_worker_and_joins_it(monkeypatch, mode):
    import sys

    original = asyncio.create_subprocess_exec
    children = []
    code = {
        "malformed": "import sys; sys.stdout.write('PRIVATE_BROKEN_WORKER_OUTPUT'); sys.stdout.flush()",
        "oversized": "import sys,time; sys.stdout.write('x'*40000); sys.stdout.flush(); time.sleep(30)",
        "nonzero": "import sys; sys.exit(9)",
        "sdk_error": 'import sys; sys.stdout.write(\'{"ok":false,"error":"LMStudioServerError","error_stage":"tokenization"}\'); sys.stdout.flush()',
    }[mode]

    async def spawn(*command, **kwargs):
        if "xaa.providers.tokenizer_worker" in command or "--tokenizer-worker" in command:
            process = await original(sys.executable, "-c", code, **kwargs)
            children.append(process)
            return process
        return await original(*command, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(PromptCountError) as error:
        await count_prompt("http://127.0.0.1:1", request(), None)
    assert len(children) == 1
    assert children[0].returncode is not None
    assert "PRIVATE_BROKEN_WORKER_OUTPUT" not in str(error.value)
    if mode == "sdk_error":
        assert error.value.worker_error_type == "LMStudioServerError"
        assert error.value.worker_error_stage == "tokenization"

import asyncio
import hashlib
import json

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.providers.streaming import CompletionAssembler, StreamAssemblyError
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


def chunk(delta, finish=None):
    return json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


def test_fragmented_stream_assembles_tool_ids_names_arguments_and_ignores_reasoning():
    assembler = CompletionAssembler()
    assert assembler.feed(chunk({"reasoning_content": "private reasoning"})) == ""
    assembler.feed(
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-",
                        "type": "function",
                        "function": {"name": "workspace_", "arguments": '{"path":"'},
                    }
                ]
            }
        )
    )
    assembler.feed(
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "1",
                        "function": {"name": "read_file", "arguments": '文件.txt"}'},
                    }
                ]
            },
            "tool_calls",
        )
    )
    assembler.feed(
        json.dumps(
            {
                "choices": [],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            }
        )
    )
    response = assembler.complete()
    call = response["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "call-1" and call["function"]["name"] == "workspace_read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "文件.txt"}
    assert "private reasoning" not in json.dumps(response)
    assert response["usage"]["total_tokens"] == 5


@pytest.mark.parametrize(
    "fragments",
    [
        [chunk({"content": "unfinished"})],
        [chunk({"reasoning_content": "private reasoning only"}, "stop")],
        [
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "a",
                            "function": {"name": "tool", "arguments": '{"path":'},
                        }
                    ]
                },
                "tool_calls",
            )
        ],
        [
            chunk(
                {
                    "tool_calls": [
                        {"index": 1, "id": "a", "function": {"name": "tool", "arguments": "{}"}}
                    ]
                },
                "tool_calls",
            )
        ],
        [
            chunk(
                {
                    "tool_calls": [
                        {"index": 0, "id": "a", "function": {"name": "tool", "arguments": "[]"}}
                    ]
                },
                "tool_calls",
            )
        ],
        [
            chunk(
                {
                    "tool_calls": [
                        {"index": 0, "id": "same", "function": {"name": "tool", "arguments": "{}"}},
                        {"index": 1, "id": "same", "function": {"name": "tool", "arguments": "{}"}},
                    ]
                },
                "tool_calls",
            )
        ],
        [chunk({"content": "😀" * 17000}, "stop")],
        [chunk({"tool_calls": [{"index": 8, "id": "a"}]}, "tool_calls")],
        [chunk({"content": "done"}, "stop"), chunk({"content": "after"})],
    ],
)
def test_incomplete_or_invalid_stream_cannot_form_completion(fragments):
    with pytest.raises(StreamAssemblyError):
        assembler = CompletionAssembler()
        for fragment in fragments:
            assembler.feed(fragment)
        assembler.complete()


@pytest.mark.parametrize(
    ("fragments", "code"),
    [
        ([chunk({"content": "unfinished"})], "incomplete_response"),
        ([chunk({"reasoning_content": "private reasoning only"}, "stop")], "empty_response"),
        (
            [chunk({"content": "ok"}, "stop"), chunk({"content": "late"})],
            "invalid_or_oversized_chunk",
        ),
    ],
)
def test_stream_assembly_errors_expose_bounded_machine_readable_code(fragments, code):
    assembler = CompletionAssembler()
    with pytest.raises(StreamAssemblyError) as captured:
        for fragment in fragments:
            assembler.feed(fragment)
        assembler.complete()
    assert captured.value.code == code


@pytest.mark.parametrize(
    "complete_arguments,finish,error_code",
    [(True, False, "incomplete_response"), (False, True, "invalid_tool_arguments")],
)
async def test_partial_stream_never_executes_a_write(
    workspace_tmp_path, complete_arguments, finish, error_code
):
    target = workspace_tmp_path / "file.txt"
    target.write_bytes(b"before")
    store = RunStore(workspace_tmp_path / "partial.sqlite3")
    run_id = store.create_run(
        objective="Edit",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
    )["id"]
    arguments = json.dumps(
        {
            "path": "file.txt",
            "content": "after",
            "expected_sha256": hashlib.sha256(b"before").hexdigest(),
        }
    )

    class PartialProvider(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def stream(self, **kwargs):
            yield chunk({"content": "Starting edit"})
            yield chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "write",
                            "function": {
                                "name": "workspace_write_file",
                                "arguments": arguments if complete_arguments else arguments[:-3],
                            },
                        }
                    ]
                },
                "tool_calls" if finish else None,
            )

    provider = PartialProvider()
    try:
        await Coordinator(store, provider, stream_responses=True).execute(run_id)
    finally:
        await provider.close()
    assert target.read_bytes() == b"before"
    assert store.get_run(run_id)["state"] == "blocked"
    history = store.conversation_messages(run_id)
    assert len(history) == 1 and history[0]["role"] == "user"
    assert "No part of that response was executed" in history[0]["content"]
    events = store.list_events(run_id)
    rejected = [event for event in events if event["event_type"] == "model.stream_rejected"]
    assert store.inference_attempts(run_id) == 2
    assert len(rejected) == 2
    assert all(event["payload"]["code"] == error_code for event in rejected)
    assert not any(event["event_type"] == "tool.intended" for event in store.list_events(run_id))


async def test_incomplete_stream_gets_one_safe_recovery_attempt(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "stream-recovery.sqlite3")
    run_id = store.create_run(
        objective="Answer after a truncated response",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
    )["id"]

    class RecoveringProvider(LMStudioProvider):
        calls = 0

        async def _stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield chunk({"content": "partial draft"})
                return
            yield chunk({"content": "Recovered answer"}, "stop")

    provider = RecoveringProvider()
    try:
        await Coordinator(store, provider, stream_responses=True).execute(run_id)
    finally:
        await provider.close()

    events = store.list_events(run_id)
    rejected = [event for event in events if event["event_type"] == "model.stream_rejected"]
    requested = [event for event in events if event["event_type"] == "model.response_requested"]
    replies = [event for event in events if event["event_type"] == "model.message"]
    assert store.get_run(run_id)["state"] == "verifying"
    assert store.inference_attempts(run_id) == 2
    assert len(rejected) == 1 and rejected[0]["payload"]["code"] == "incomplete_response"
    assert len(requested) == 1
    assert "No part of that response was executed" in requested[0]["payload"]["message"]["content"]
    assert replies[-1]["payload"]["message"]["content"] == "Recovered answer"


async def test_cancelling_stream_releases_inference_queue(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "cancel.sqlite3")
    run_id = store.create_run(
        objective="Wait", acceptance_criteria=[], workspace=None, mode="observe", model="fixture"
    )["id"]

    class WaitingProvider(LMStudioProvider):
        calls = 0

        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.closed = asyncio.Event()

        async def _stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                try:
                    yield chunk({"content": "Partial"})
                    self.started.set()
                    await asyncio.Event().wait()
                finally:
                    self.closed.set()
            else:
                yield chunk({"content": "Recovered"}, "stop")

    provider = WaitingProvider()
    task = asyncio.create_task(Coordinator(store, provider, stream_responses=True).execute(run_id))
    try:
        await asyncio.wait_for(provider.started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.closed.is_set()
        response = await asyncio.wait_for(provider.chat_streamed(model="fixture", messages=[]), 2)
        assert response["choices"][0]["message"]["content"] == "Recovered"
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "paused"
    assert store.get_run(run_id)["lease_owner"] is None

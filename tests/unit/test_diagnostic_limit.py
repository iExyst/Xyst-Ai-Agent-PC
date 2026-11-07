import json
import re

import httpx
import pytest
from xaa.providers.lm_studio import (
    DIAGNOSTIC_OUTPUT_TOKENS,
    DiagnosticLimitError,
    LMStudioProvider,
    ProviderError,
)
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


@pytest.mark.parametrize("truncated_at", [None, 1, 2])
async def test_diagnostic_wire_cap_and_no_cached_truncation(truncated_at):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["max_tokens"] == DIAGNOSTIC_OUTPUT_TOKENS
        if len(requests) == 1:
            nonce = re.search(r"value ([0-9a-f]{32})", payload["messages"][0]["content"]).group(1)
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "echo",
                        "type": "function",
                        "function": {"name": "xaa_echo", "arguments": json.dumps({"value": nonce})},
                    }
                ],
            }
        else:
            message = {"role": "assistant", "content": payload["messages"][-1]["content"]}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "length" if len(requests) == truncated_at else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    provider = LMStudioProvider()
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(handle)
    )
    try:
        if truncated_at is not None:
            with pytest.raises(DiagnosticLimitError):
                await provider.diagnose_tools("fixture")
            assert len(requests) == truncated_at
            assert "fixture" not in provider._diagnosed_models
        else:
            assert await provider.diagnose_tools("fixture")
            assert await provider.diagnose_tools("fixture")
            assert len(requests) == 2
    finally:
        await provider.close()


@pytest.mark.parametrize("transient_status", [408, 429, 500, 502, 503, 504])
async def test_tool_diagnostic_retries_a_transient_chat_status_once(transient_status):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(transient_status)
        if payload.get("tools"):
            nonce = re.search(r"value ([0-9a-f]{32})", payload["messages"][0]["content"]).group(1)
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "echo",
                        "type": "function",
                        "function": {"name": "xaa_echo", "arguments": json.dumps({"value": nonce})},
                    }
                ],
            }
        else:
            message = {"role": "assistant", "content": payload["messages"][-1]["content"]}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    provider = LMStudioProvider()
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(handle)
    )
    try:
        assert await provider.diagnose_tools("fixture")
        assert len(requests) == 3
        assert "fixture" in provider._diagnosed_models
    finally:
        await provider.close()


async def test_tool_diagnostic_does_not_retry_a_permanent_chat_rejection():
    requests = 0

    def handle(_request):
        nonlocal requests
        requests += 1
        return httpx.Response(400)

    provider = LMStudioProvider()
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(ProviderError, match=r"rejected chat request \(400\)"):
            await provider.diagnose_tools("fixture")
        assert requests == 1
        assert "fixture" not in provider._diagnosed_models
    finally:
        await provider.close()


async def test_tool_diagnostic_retries_lm_studio_engine_fetch_failure_once():
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                400,
                json={"error": {"message": "Engine protocol predict request failed: fetch failed"}},
            )
        if payload.get("tools"):
            nonce = re.search(r"value ([0-9a-f]{32})", payload["messages"][0]["content"]).group(1)
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "echo",
                        "type": "function",
                        "function": {"name": "xaa_echo", "arguments": json.dumps({"value": nonce})},
                    }
                ],
            }
        else:
            message = {"role": "assistant", "content": payload["messages"][-1]["content"]}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    provider = LMStudioProvider()
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(handle)
    )
    try:
        assert await provider.diagnose_tools("fixture")
        assert len(requests) == 3
        assert "fixture" in provider._diagnosed_models
    finally:
        await provider.close()


async def test_truncated_run_diagnostic_records_usage_and_blocks_before_tools(workspace_tmp_path):
    class Truncated(LMStudioProvider):
        async def chat(self, *, model, messages, tools=None, max_output_tokens=None):
            assert max_output_tokens == 512
            return {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "incomplete"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 512, "total_tokens": 532},
            }

    store = RunStore(workspace_tmp_path / "truncated.sqlite3")
    run = store.create_run(
        objective="Read files",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )
    provider = Truncated()
    try:
        await Coordinator(store, provider).execute(run["id"])
    finally:
        await provider.close()
    events = store.list_events(run["id"])
    assert store.get_run(run["id"])["state"] == "blocked"
    assert store.token_usage(run["id"])["total_tokens"] == 532
    assert any(e["payload"].get("reason") == "diagnostic_output_limit" for e in events)
    assert not any(e["event_type"] in {"tool.intended", "model.inference.started"} for e in events)
    assert "fixture" not in provider._diagnosed_models

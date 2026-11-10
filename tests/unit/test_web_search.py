import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime import web_search
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.web_search import WEB_SEARCH_METADATA, WEB_SEARCH_TOOL
from xaa.storage.runs import RunStore


def test_web_search_results_are_bounded_and_untrusted() -> None:
    payload = {
        "web": {
            "results": [
                {"title": "Guide", "url": "https://docs.example/guide", "description": "Text"},
                {"title": "Non-HTTPS", "url": "http://docs.example/", "description": "Skip"},
                {
                    "title": "Credential URL",
                    "url": "https://user:pass@docs.example/",
                    "description": "Skip",
                },
                {
                    "title": "Credential query",
                    "url": "https://docs.example/?token=secret",
                    "description": "Skip",
                },
                {"title": "IP literal", "url": "https://127.0.0.1/", "description": "Skip"},
                {"title": "Second", "url": "https://other.example/", "description": "Other"},
            ]
        }
    }
    assert web_search._public_results(payload, 1) == [
        {
            "title": "Guide",
            "url": "https://docs.example/guide",
            "description": "Text",
            "trust": "untrusted_web_content",
        }
    ]
    assert (
        web_search.WebSearchArguments(query="  official docs  ", count=3).query == "official docs"
    )
    with pytest.raises(ValueError):
        web_search.WebSearchArguments(query="   ")
    with pytest.raises(ValueError, match="1–75 words"):
        web_search.WebSearchArguments(query=" ".join(["word"] * 76))


def test_web_search_contract_covers_serialized_output_and_item_bounds() -> None:
    assert WEB_SEARCH_METADATA["output_size_bytes"] == web_search.MAX_RESULT_BYTES
    assert WEB_SEARCH_METADATA["limits"] == {
        "response_bytes": web_search.MAX_RESPONSE_BYTES,
        "result_bytes": web_search.MAX_RESULT_BYTES,
        "results": web_search.MAX_RESULTS,
        "title_characters": web_search.MAX_TITLE_CHARS,
        "url_characters": web_search.MAX_URL_CHARS,
        "description_characters": web_search.MAX_DESCRIPTION_CHARS,
        "query_characters": web_search.MAX_QUERY_CHARS,
    }
    assert WEB_SEARCH_TOOL["function"]["parameters"] == WEB_SEARCH_METADATA["input_schema"]
    output = WEB_SEARCH_METADATA["output_schema"]
    item_properties = output["properties"]["results"]["items"]["properties"]
    assert item_properties["title"]["maxLength"] == web_search.MAX_TITLE_CHARS
    assert item_properties["url"]["maxLength"] == web_search.MAX_URL_CHARS
    assert item_properties["description"]["maxLength"] == web_search.MAX_DESCRIPTION_CHARS
    assert output["properties"]["error"]["maxLength"] == 500
    assert len(output["allOf"]) == 2


def test_web_search_replaces_oversized_unicode_results_with_bounded_failure() -> None:
    result = {
        "results": [
            {
                "title": "😀" * web_search.MAX_TITLE_CHARS,
                "url": "https://example.test/" + "😀" * (web_search.MAX_URL_CHARS - 20),
                "description": "😀" * web_search.MAX_DESCRIPTION_CHARS,
                "trust": "untrusted_web_content",
            }
            for _ in range(web_search.MAX_RESULTS + 1)
        ],
        "trust": "untrusted_web_content",
        "status": "ok",
    }
    bounded = web_search._bounded_result(result)
    assert bounded["status"] == "read_failed"
    assert len(json.dumps(bounded).encode("ascii")) <= web_search.MAX_RESULT_BYTES


@pytest.mark.asyncio
async def test_brave_request_uses_fixed_endpoint_secret_header_and_safe_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Docs",
                            "url": "https://docs.example/path#section",
                            "description": "Found it",
                        }
                    ]
                }
            },
        )

    monkeypatch.setattr(
        web_search.httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(handler)
    )
    result = await web_search._search("fixture query", 4, "secret-api-key")
    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "api.search.brave.com"
    assert request.url.path == "/res/v1/web/search"
    assert request.url.params["q"] == "fixture query"
    assert request.url.params["count"] == "4"
    assert request.headers["X-Subscription-Token"] == "secret-api-key"
    assert result["results"] == [
        {
            "title": "Docs",
            "url": "https://docs.example/path",
            "description": "Found it",
            "trust": "untrusted_web_content",
        }
    ]
    assert "secret-api-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_search_saves_bounded_untrusted_result_in_tool_journal(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(workspace_tmp_path / "search.sqlite3")
    run = store.create_run(
        objective="Search docs",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture/model",
        approved_web_search=True,
    )
    token = store.acquire_lease(run["id"], "search-test")
    assert token is not None

    async def fake_search(query: str, count: int, api_key: str) -> dict[str, object]:
        assert query == "fixture query" and count == 3 and api_key == "private-test-key"
        return {
            "results": [{"url": "https://docs.example/", "trust": "untrusted_web_content"}],
            "trust": "untrusted_web_content",
            "status": "ok",
        }

    monkeypatch.setattr(web_search, "_search", fake_search)
    result = await web_search.search_web(
        store,
        run["id"],
        token,
        "search-call-1",
        "fixture query",
        3,
        "private-test-key",
        deadline=time.monotonic() + 5,
    )
    assert result["trust"] == "untrusted_web_content"
    assert store.unresolved_tool_calls(run["id"]) == []
    with store.connection() as connection:
        saved = connection.execute(
            "SELECT tool_name,result FROM tool_calls WHERE provider_call_id=?", ("search-call-1",)
        ).fetchone()
    assert saved["tool_name"] == "web.search"
    assert "untrusted_web_content" in saved["result"]
    assert "private-test-key" not in saved["result"]
    store.release_lease(run["id"], token)


@pytest.mark.asyncio
async def test_invalid_provider_response_is_a_saved_failure(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(workspace_tmp_path / "search-invalid.sqlite3")
    run = store.create_run(
        objective="Search docs",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture/model",
        approved_web_search=True,
    )
    token = store.acquire_lease(run["id"], "search-invalid-test")
    assert token is not None

    async def invalid_response(query: str, count: int, api_key: str) -> dict[str, object]:
        del query, count, api_key
        raise TypeError("invalid provider response shape")

    monkeypatch.setattr(web_search, "_search", invalid_response)
    result = await web_search.search_web(
        store,
        run["id"],
        token,
        "search-invalid-call",
        "fixture query",
        5,
        "private-test-key",
        deadline=time.monotonic() + 5,
    )
    assert result["status"] == "read_failed"
    assert "invalid provider response shape" in result["error"]
    assert store.unresolved_tool_calls(run["id"]) == []
    store.release_lease(run["id"], token)


def test_search_tool_requires_per_run_permission_key_and_workspace(
    workspace_tmp_path: Path,
) -> None:
    class Credentials:
        def get(self, profile: str = "lm-studio") -> str | None:
            return "configured-key" if profile == "brave-search" else None

    store = RunStore(workspace_tmp_path / "search-tool-gate.sqlite3")
    coordinator = Coordinator(
        store,
        LMStudioProvider(),
        credential_store=Credentials(),  # type: ignore[arg-type]
    )
    run = store.create_run(
        objective="Search",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture/model",
        approved_web_search=True,
    )
    exposed = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in coordinator._tools(run["id"], run)
    }
    assert exposed["web_search"] == WEB_SEARCH_METADATA["input_schema"]
    run["approved_web_search"] = False
    assert "web_search" not in {
        tool["function"]["name"] for tool in coordinator._tools(run["id"], run)
    }
    run["approved_web_search"] = True
    run["workspace"] = None
    assert coordinator._tools(run["id"], run) == []


@pytest.mark.asyncio
async def test_coordinator_executes_search_only_with_saved_run_and_provider_grants(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Credentials:
        def get(self, profile: str = "lm-studio") -> str | None:
            return "configured-key" if profile == "brave-search" else None

    async def fake_search(query: str, count: int, api_key: str) -> dict[str, object]:
        assert (query, count, api_key) == ("official docs", 2, "configured-key")
        return {"results": [], "trust": "untrusted_web_content", "status": "ok"}

    monkeypatch.setattr(web_search, "_search", fake_search)
    store = RunStore(workspace_tmp_path / "search-coordinator.sqlite3")
    run = store.create_run(
        objective="Search official docs",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture/model",
        approved_web_search=True,
    )
    token = store.acquire_lease(run["id"], "search-coordinator-test")
    assert token is not None
    coordinator = Coordinator(
        store,
        LMStudioProvider(),
        credential_store=Credentials(),  # type: ignore[arg-type]
    )
    result = await coordinator._execute_tool(
        run["id"],
        token,
        run,
        1,
        time.monotonic() + 5,
        {
            "id": "search-coordinator-call",
            "function": {
                "name": "web_search",
                "arguments": '{"query":"official docs","count":2}',
            },
        },
        coordinator._tools(run["id"], run),
    )
    assert result["status"] == "ok"
    assert result["trust"] == "untrusted_web_content"
    assert store.unresolved_tool_calls(run["id"]) == []


@pytest.mark.asyncio
async def test_search_cancellation_is_journaled_before_propagation(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(workspace_tmp_path / "search-cancel.sqlite3")
    run = store.create_run(
        objective="Search docs",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture/model",
        approved_web_search=True,
    )
    token = store.acquire_lease(run["id"], "search-cancel-test")
    assert token is not None
    started = asyncio.Event()

    async def slow_search(query: str, count: int, api_key: str) -> dict[str, object]:
        del query, count, api_key
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(web_search, "_search", slow_search)
    task = asyncio.create_task(
        web_search.search_web(
            store,
            run["id"],
            token,
            "search-cancel-call",
            "fixture query",
            3,
            "private-test-key",
            deadline=time.monotonic() + 5,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.unresolved_tool_calls(run["id"]) == []
    with store.connection() as connection:
        saved = connection.execute(
            "SELECT result FROM tool_calls WHERE provider_call_id=?", ("search-cancel-call",)
        ).fetchone()
    assert '"status": "cancelled"' in saved["result"]
    store.release_lease(run["id"], token)

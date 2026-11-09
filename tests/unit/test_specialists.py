import asyncio
import json
import time

import pytest
from xaa.providers.lm_studio import ContextLimitError, LMStudioProvider
from xaa.providers.streaming import StreamAssemblyError
from xaa.runtime import specialists as specialist_module
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.task_review import candidate_evidence
from xaa.storage.memory import MemoryStore
from xaa.storage.runs import RunStore
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.tasks import TaskStore


@pytest.mark.parametrize(
    "threshold,output_limit,pre_reserve",
    [(None, None, False), (20, 8, False), (100, 8, True)],
)
async def test_independent_specialists_read_scoped_files_without_polluting_parent_history(
    workspace_tmp_path,
    monkeypatch,
    threshold,
    output_limit,
    pre_reserve,
):
    store = RunStore(workspace_tmp_path / "specialists.sqlite3")
    for name in ("a", "b"):
        (workspace_tmp_path / f"{name}.txt").write_text(f"evidence-{name}")
    run_id = store.create_run(
        objective="Parent context must not be copied",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_total_tokens=threshold,
        max_output_tokens=output_limit,
        pre_inference_token_reservations=pre_reserve,
    )["id"]
    parent = store.acquire_lease(run_id, "coordinator")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id=name,
                objective=name,
                max_iterations=2,
                max_total_tokens=10 if threshold is not None and not pre_reserve else None,
                max_output_tokens=4 if output_limit is not None else None,
            )
            for name in ("a", "b")
        ],
    )

    active_workspace_calls = 0
    max_active_workspace_calls = 0
    execute_filesystem = specialist_module.execute_filesystem

    async def delayed_filesystem(*args, **kwargs):
        nonlocal active_workspace_calls, max_active_workspace_calls
        active_workspace_calls += 1
        max_active_workspace_calls = max(max_active_workspace_calls, active_workspace_calls)
        try:
            await asyncio.sleep(0.03)
            return await execute_filesystem(*args, **kwargs)
        finally:
            active_workspace_calls -= 1

    monkeypatch.setattr(specialist_module, "execute_filesystem", delayed_filesystem)

    class Reader(LMStudioProvider):
        async def chat(
            self,
            *,
            model,
            messages,
            tools=None,
            max_output_tokens=None,
            on_admission=None,
            require_exact_context=False,
        ):
            if pre_reserve:
                assert require_exact_context is True and on_admission is not None
                # The exact prompt count is asynchronous, so both independent attempts can be
                # unmeasured before either receives its shared-budget reservation.
                await asyncio.sleep(0.01)
                on_admission(3, max_output_tokens)
            assert max_output_tokens == (4 if output_limit is not None else None)
            assert "Parent context must not be copied" not in json.dumps(messages)
            assert {tool["function"]["name"] for tool in tools} == {
                "workspace_read_file",
                "workspace_list_directory",
            }
            await asyncio.sleep(0.01)
            if messages[-1]["role"] == "user":
                name = json.loads(messages[-1]["content"])["objective"]
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "same-id",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": json.dumps({"path": f"{name}.txt"}),
                            },
                        }
                    ],
                }
            else:
                result = json.loads(messages[-1]["content"])
                message = {"role": "assistant", "content": result["text"]}
            return {
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

    provider = Reader()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()
    assert statuses == [
        {"task_id": "a", "status": "verifying"},
        {"task_id": "b", "status": "verifying"},
    ]
    assert [json.loads(row["result"])["candidate"] for row in tasks.records(run_id)] == [
        "evidence-a",
        "evidence-b",
    ]
    assert store.conversation_messages(run_id) == []
    assert not store.unresolved_tool_calls(run_id)
    assert store.token_usage(run_id)["total_tokens"] == 20
    assert (max_active_workspace_calls > 1) is (threshold is None or pre_reserve)
    if pre_reserve:
        report = store.usage_report(run_id)
        assert report["outstanding_reservations"] == 0
        assert (
            len(
                [
                    event
                    for event in store.list_events(run_id)
                    if event["event_type"] == "tokens.reserved"
                ]
            )
            == 4
        )


@pytest.mark.parametrize(
    "tool_name,permission,approvals,arguments,journal_name",
    [
        (
            "web_search",
            "web.search",
            {"approved_web_search": True},
            '{"query":"fixture fact","count":1}',
            "web.search",
        ),
        (
            "web_fetch",
            "web.read",
            {"approved_web_hosts": ["example.com"]},
            '{"url":"https://example.com/fact"}',
            "web.fetch",
        ),
        (
            "browser_run",
            "web.browser.read",
            {"approved_browser": True, "approved_web_hosts": ["example.com"]},
            '{"url":"https://example.com/fact","screenshot":true}',
            "browser.run",
        ),
    ],
)
async def test_researcher_uses_only_run_approved_task_scoped_web_reads(
    workspace_tmp_path, monkeypatch, tool_name, permission, approvals, arguments, journal_name
):
    from xaa.runtime import web_fetch, web_search

    store = RunStore(workspace_tmp_path / "specialist-web.sqlite3")
    run_id = store.create_run(
        objective="Research approved sources",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        **approvals,
    )["id"]
    parent = store.acquire_lease(run_id, "coordinator")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="research",
                objective="Read an approved fixture source",
                role="researcher",
                permissions=("workspace.read", permission),
                max_iterations=2,
            )
        ],
    )
    requested: list[tuple[str, str]] = []

    async def fake_search(query: str, count: int, api_key: str):
        requested.append((query, api_key))
        return {
            "status": "ok",
            "trust": "untrusted_web_content",
            "results": [{"url": "https://example.com/fact", "title": "Fixture fact"}],
        }

    async def fake_read(url: str, hosts: set[str]):
        requested.append((url, ",".join(sorted(hosts))))
        return {
            "status": "ok",
            "trust": "untrusted_web_content",
            "text": "Fixture fact",
        }

    async def fake_browser(scoped_store, run_id, parent, journal_id, arguments, hosts, *, deadline):
        requested.append((arguments["url"], ",".join(hosts)))
        assert arguments.get("screenshot") is (tool_name == "browser_run")
        assert scoped_store.record_tool_intent(
            run_id,
            parent,
            call_id=journal_id,
            tool="browser.run",
            version="1",
            arguments=arguments,
            idempotency_key=journal_id,
        )
        result = {
            "status": "ok",
            "trust": "untrusted_web_content",
            "text": "Fixture fact",
        }
        if arguments.get("screenshot"):
            result["screenshot_artifact"] = {
                "id": "fixture-screenshot",
                "run_id": run_id,
                "media_type": "image/png",
                "size_bytes": 3,
                "sha256": "a" * 64,
                "provenance": "browser.screenshot:untrusted_web_content",
                "created_at": "2026-11-08T12:00:00+00:00",
            }
        scoped_store.record_tool_result(run_id, parent, journal_id, result)
        return result

    monkeypatch.setattr(web_search, "_search", fake_search)
    monkeypatch.setattr(web_fetch, "_read_url", fake_read)
    monkeypatch.setattr(specialist_module, "run_approved_browser", fake_browser)

    class Credentials:
        def get(self, name: str) -> str | None:
            assert name == "brave-search"
            return "configured-test-key"

    class Researcher(LMStudioProvider):
        async def chat(self, *, messages, tools=None, **kwargs):
            names = {tool["function"]["name"] for tool in tools or []}
            assert tool_name in names
            assert ({"web_search", "web_fetch"} - {tool_name}).isdisjoint(names)
            if tool_name == "browser_run":
                browser_schema = next(
                    tool["function"]["parameters"]
                    for tool in tools or []
                    if tool["function"]["name"] == tool_name
                )
                assert "screenshot" in browser_schema["properties"]
            if messages[-1]["role"] == "user":
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "research-read",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            else:
                result = json.loads(messages[-1]["content"])
                assert (
                    result.get("text") == "Fixture fact"
                    or result.get("results", [{}])[0].get("title") == "Fixture fact"
                )
                message = {"role": "assistant", "content": "Read the fixture fact."}
            return {
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

    provider = Researcher()
    try:
        outcomes = await SpecialistRunner(
            store, provider, credential_store=Credentials()
        ).run_ready(run_id, parent, deadline=time.monotonic() + 30)
    finally:
        await provider.close()

    assert outcomes == [{"task_id": "research", "status": "verifying"}]
    assert requested == (
        [("fixture fact", "configured-test-key")]
        if tool_name == "web_search"
        else [("https://example.com/fact", "example.com")]
    )
    with store.connection() as connection:
        journal = connection.execute(
            "SELECT task_id,tool_name,state,result FROM tool_calls WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert journal["task_id"] == f"{run_id}:research"
    assert (journal["tool_name"], journal["state"]) == (journal_name, "completed")
    assert json.loads(journal["result"])["trust"] == "untrusted_web_content"
    if tool_name == "browser_run":
        assert json.loads(journal["result"])["screenshot_artifact"]["id"] == "fixture-screenshot"
        task_results = [
            event["payload"]["result"]
            for event in store.list_events(run_id)
            if event["event_type"] == "task.tool.result"
        ]
        assert task_results[0]["screenshot_artifact"]["id"] == "fixture-screenshot"
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.asyncio
async def test_researcher_uses_only_inherited_user_approved_mcp_reads(workspace_tmp_path):
    import sys
    from pathlib import Path

    from xaa.runtime.mcp_manager import MCPManager
    from xaa.storage.mcp import MCPRegistry, MCPServerSpec

    store = RunStore(workspace_tmp_path / "specialist-mcp.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(
        MCPServerSpec(
            name="Fixture",
            command=sys.executable,
            args=[str(Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py")],
        )
    )
    manager = MCPManager(store, None)
    await manager.connect_server(server.id)
    tools = registry.list_tools(server.id)
    read_tool = next(tool for tool in tools if tool.name == "add")
    write_tool = next(tool for tool in tools if tool.name == "slow")
    read_tool = registry.set_permission(server.id, read_tool.name, "read", read_tool.schema_sha256)
    write_tool = registry.set_permission(
        server.id, write_tool.name, "write", write_tool.schema_sha256
    )

    run_id = store.create_run(
        objective="Read a configured MCP fixture",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "coordinator")
    tasks = TaskStore(store)
    task = TaskDefinition(
        id="research",
        objective="Use the MCP read tool",
        role="researcher",
        permissions=("workspace.read", read_tool.qualified_name),
        max_iterations=2,
    )
    with pytest.raises(PermissionError):
        tasks.validate_plan(
            run_id,
            [
                task.model_copy(
                    update={"permissions": ("workspace.read", write_tool.qualified_name)}
                )
            ],
        )
    tasks.save_plan(run_id, parent, [task])

    class Researcher(LMStudioProvider):
        async def chat(self, *, messages, tools=None, **kwargs):
            names = {item["function"]["name"] for item in tools or []}
            assert read_tool.qualified_name in names
            assert write_tool.qualified_name not in names
            if messages[-1]["role"] == "user":
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "mcp-read",
                            "type": "function",
                            "function": {
                                "name": read_tool.qualified_name,
                                "arguments": '{"left":20,"right":22}',
                            },
                        }
                    ],
                }
            else:
                result = json.loads(messages[-1]["content"])
                assert result["structured_content"] == {"result": 42}
                message = {"role": "assistant", "content": "The MCP read returned 42."}
            return {
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

    provider = Researcher()
    try:
        outcomes = await SpecialistRunner(store, provider, mcp_manager=manager).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()
        await manager.close()

    assert outcomes == [{"task_id": "research", "status": "verifying"}]
    with store.connection() as connection:
        journal = connection.execute(
            "SELECT task_id,tool_name,state,arguments,result FROM tool_calls WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert journal["task_id"] == f"{run_id}:research"
    assert (journal["tool_name"], journal["state"]) == ("mcp.call", "completed")
    journal_arguments = json.loads(journal["arguments"])
    assert journal_arguments["tool"] == read_tool.qualified_name
    assert journal_arguments["permission"] == "read"
    assert journal_arguments["schema_sha256"] == read_tool.schema_sha256
    assert json.loads(journal["result"])["structured_content"] == {"result": 42}
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.parametrize("permission", ["web.search", "web.read", "web.browser.read"])
def test_specialist_plan_cannot_inherit_unapproved_web_access(workspace_tmp_path, permission):
    store = RunStore(workspace_tmp_path / "unapproved-specialist-web.sqlite3")
    run_id = store.create_run(
        objective="No web approval",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    with pytest.raises(PermissionError):
        TaskStore(store).validate_plan(
            run_id,
            [
                TaskDefinition(
                    id="research",
                    objective="Search the web",
                    role="researcher",
                    permissions=("workspace.read", permission),
                )
            ],
        )


async def test_specialist_write_request_fails_without_modifying_file(workspace_tmp_path):
    target = workspace_tmp_path / "target.txt"
    target.write_bytes(b"unchanged")
    store = RunStore(workspace_tmp_path / "denied.sqlite3")
    run_id = store.create_run(
        objective="Parent can write",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(id="read", objective="Read only", max_iterations=1),
            TaskDefinition(
                id="dependent", objective="Wait", dependencies=["read"], max_iterations=1
            ),
        ],
    )

    class Writer(LMStudioProvider):
        async def chat(self, **kwargs):
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "bad",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_write_file",
                                        "arguments": '{"path":"target.txt","content":"changed"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    provider = Writer()
    try:
        runner = SpecialistRunner(store, provider)
        await runner.run_ready(run_id, parent, deadline=time.monotonic() + 30)
        await runner.run_ready(run_id, parent, deadline=time.monotonic() + 30)
    finally:
        await provider.close()
    assert target.read_bytes() == b"unchanged"
    assert [row["status"] for row in tasks.records(run_id)] == ["blocked", "failed"]
    failure = json.loads(
        next(row for row in tasks.records(run_id) if row["local_id"] == "read")["result"]
    )
    assert failure["causes"] == ["PermissionError"]


async def test_context_budget_reason_is_persisted_for_blocked_specialist(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "context-block.sqlite3")
    run_id = store.create_run(
        objective="Bounded context",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="limited", objective="Read evidence")])

    class ContextLimitedProvider(LMStudioProvider):
        async def chat(self, **kwargs):
            raise ContextLimitError(
                "Fixture context limit",
                reason="prompt_count_unavailable",
                counter_error_type="PromptCounterUnavailable",
                counter_error_stage="model_inventory",
                counter_error_code="websocket_timeout",
            )

    provider = ContextLimitedProvider()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()
    assert statuses == [{"task_id": "limited", "status": "blocked"}]
    result = json.loads(tasks.records(run_id)[0]["result"])
    assert result["causes"] == ["ContextLimitError"]
    assert result["failure_codes"] == ["prompt_count_unavailable", "websocket_timeout"]
    assert result["failure_details"] == [
        {
            "reason": "prompt_count_unavailable",
            "counter_error_type": "PromptCounterUnavailable",
            "counter_error_stage": "model_inventory",
            "counter_error_code": "websocket_timeout",
        }
    ]


async def test_provider_timeout_is_classified_as_specialist_inference_failure(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-timeout.sqlite3")
    run_id = store.create_run(
        objective="Parent task",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="timeout", objective="Read the available file")],
    )

    class TimedOutProvider(LMStudioProvider):
        async def chat(self, **kwargs):
            raise TimeoutError("fixture timeout detail is not persisted")

    provider = TimedOutProvider()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()

    assert statuses == [{"task_id": "timeout", "status": "failed"}]
    result = json.loads(tasks.records(run_id)[0]["result"])
    assert result["causes"] == ["SpecialistInferenceTimeout"]
    assert result["failure_details"] == [{"stage": "chat"}]
    assert "fixture timeout detail" not in json.dumps(result)
    events = store.list_events(run_id)
    assert sum(event["event_type"] == "task.inference.started" for event in events) == 1
    assert sum(event["event_type"] == "task.result" for event in events) == 1


async def test_invalid_private_read_path_returns_tool_error_and_can_be_corrected(
    workspace_tmp_path,
):
    project = workspace_tmp_path / "project"
    project.mkdir()
    (project / "evidence.txt").write_text("private evidence", encoding="utf-8")
    data = workspace_tmp_path / "data"
    data.mkdir()
    store = RunStore(data / "invalid-private-read.sqlite3")
    run_id = store.create_run(
        objective="Read the private evidence file",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="reader",
                objective="Read evidence.txt and report its text",
                permissions=("workspace.read", "workspace.write"),
                max_iterations=4,
            )
        ],
    )

    class CorrectingProvider(LMStudioProvider):
        async def chat(self, *, messages, **kwargs):
            if messages[-1]["role"] == "user":
                call = {
                    "id": "bad-path",
                    "type": "function",
                    "function": {
                        "name": "workspace_read_file",
                        "arguments": json.dumps({"path": "/evidence.txt"}),
                    },
                }
                return {
                    "choices": [{"message": {"role": "assistant", "tool_calls": [call]}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            result = json.loads(messages[-1]["content"])
            if result.get("error") == "invalid_path":
                call = {
                    "id": "good-path",
                    "type": "function",
                    "function": {
                        "name": "workspace_read_file",
                        "arguments": json.dumps({"path": "evidence.txt"}),
                    },
                }
                return {
                    "choices": [{"message": {"role": "assistant", "tool_calls": [call]}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Read and confirmed: private evidence",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    provider = CorrectingProvider()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()

    assert statuses == [{"task_id": "reader", "status": "verifying"}], json.dumps(
        {"tasks": tasks.records(run_id), "events": store.list_events(run_id)}, indent=2
    )
    events = store.list_events(run_id)
    invalid = next(
        event["payload"]
        for event in events
        if event["event_type"] == "task.tool.result"
        and event["payload"].get("call_id") == "bad-path"
    )
    assert invalid["result"]["error"] == "invalid_path"
    evidence = candidate_evidence(store, run_id, "reader")
    assert [item["path"] for item in evidence["evidence"]] == ["evidence.txt"]
    assert not store.unresolved_tool_calls(run_id)


async def test_shared_budget_is_charged_before_specialist_inference(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "budget.sqlite3")
    run_id = store.create_run(
        objective="Budget",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_iterations=1,
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="task", objective="Read", max_iterations=1)])
    store.append_worker_event(
        run_id,
        parent,
        "model.message",
        {"message": {"role": "assistant", "content": "Parent used the budget"}},
    )

    class NoCalls(LMStudioProvider):
        async def chat(self, **kwargs):
            raise AssertionError("No budget remains for specialist inference")

    provider = NoCalls()
    try:
        await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()
    assert tasks.records(run_id)[0]["status"] == "failed"
    assert not any(
        event["event_type"] == "task.inference.started" for event in store.list_events(run_id)
    )


async def test_enabled_specialist_stream_is_task_scoped_and_saved_before_final_message(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "specialist-stream.sqlite3")
    run_id = store.create_run(
        objective="Parent objective stays private to its specialist",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="research", objective="Inspect evidence", max_iterations=1)],
    )
    draft = "specialist evidence " * 20

    class StreamingResearcher(LMStudioProvider):
        async def chat_streamed(self, *, on_content, **kwargs):
            assert "Parent objective stays private" not in json.dumps(kwargs["messages"])
            on_content(draft)
            return {
                "choices": [{"message": {"role": "assistant", "content": draft}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            }

        async def chat(self, **kwargs):
            raise AssertionError(
                "Streaming specialist inference must use the streaming provider path"
            )

    provider = StreamingResearcher()
    try:
        result = await SpecialistRunner(store, provider, stream_responses=True).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()

    assert result == [{"task_id": "research", "status": "verifying"}]
    events = store.public_events(run_id)
    deltas = [event for event in events if event["event_type"] == "task.model.delta"]
    final = next(event for event in events if event["event_type"] == "task.model.message")
    outcome = next(event for event in events if event["event_type"] == "task.result")
    assert "".join(event["payload"]["text"] for event in deltas) == draft
    assert all(len(event["payload"]["text"]) <= 128 for event in deltas)
    assert all(event["payload"]["task_id"] == "research" for event in deltas)
    assert all(set(event["payload"]) == {"task_id", "inference_id", "text"} for event in deltas)
    assert deltas[0]["sequence"] < final["sequence"] < outcome["sequence"]
    assert json.loads(tasks.records(run_id)[0]["result"])["candidate"] == draft


async def test_specialist_retries_one_invalid_stream_within_its_saved_budget(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-stream-recovery.sqlite3")
    run_id = store.create_run(
        objective="Parent objective stays private to its specialist",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="research", objective="Inspect evidence", max_iterations=2)],
    )

    class RecoveringResearcher(LMStudioProvider):
        calls = 0

        async def chat_streamed(self, *, on_content, **kwargs):
            self.calls += 1
            if self.calls == 1:
                on_content("Unverified partial draft")
                raise StreamAssemblyError("invalid stream", code="invalid_or_oversized_chunk")
            assert "No part of it was executed" in kwargs["messages"][-1]["content"]
            on_content("Recovered candidate")
            return {
                "choices": [{"message": {"role": "assistant", "content": "Recovered candidate"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

    provider = RecoveringResearcher()
    try:
        result = await SpecialistRunner(store, provider, stream_responses=True).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()

    assert result == [{"task_id": "research", "status": "verifying"}]
    events = store.list_events(run_id)
    rejected = [event for event in events if event["event_type"] == "task.model.stream_rejected"]
    requested = [event for event in events if event["event_type"] == "task.response_requested"]
    assert len([event for event in events if event["event_type"] == "task.inference.started"]) == 2
    assert len(rejected) == 1
    assert rejected[0]["payload"]["code"] == "invalid_or_oversized_chunk"
    assert len(requested) == 1
    assert json.loads(tasks.records(run_id)[0]["result"]) == {"candidate": "Recovered candidate"}
    assert not any(event["event_type"] == "tool.intended" for event in events)


async def test_coordinator_dispatches_saved_plan_and_receives_candidate_status(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "dispatch.sqlite3")
    run_id = store.create_run(
        objective="Execute the saved plan",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "planner")
    SpecialistProfileStore(store).set_instructions(
        "researcher", "Prefer primary sources and cite file paths."
    )
    SpecialistProfileStore(store).set_display_name("researcher", "Source Analyst")
    MemoryStore(store).save_preference(
        "specialist:researcher", "Use the checked-in API contract as the source of truth."
    )
    TaskStore(store).save_plan(
        run_id,
        parent,
        [TaskDefinition(id="research", objective="Inspect", role="researcher", max_iterations=1)],
    )
    store.release_lease(run_id, parent)

    class DispatchProvider(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat_streamed(self, *, model, messages, tools=None, on_content=None):
            response = await self.chat(model=model, messages=messages, tools=tools)
            content = response["choices"][0]["message"].get("content")
            if content and on_content is not None:
                on_content(content)
            return response

        async def chat(self, *, model, messages, tools=None):
            if messages[0]["content"].startswith("You are "):
                assert messages[0]["content"].startswith(
                    "You are Source Analyst (researcher specialist)."
                )
                assert "Prefer primary sources and cite file paths." in messages[0]["content"]
                assert (
                    "Use the checked-in API contract as the source of truth."
                    in messages[1]["content"]
                )
                message = {"role": "assistant", "content": "Specialist candidate awaiting review"}
            elif messages[-1]["role"] == "user":
                assert any(tool["function"]["name"] == "plan_run_ready" for tool in tools)
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "dispatch",
                            "type": "function",
                            "function": {"name": "plan_run_ready", "arguments": "{}"},
                        }
                    ],
                }
            else:
                result = json.loads(messages[-1]["content"])
                assert result["tasks"] == [{"task_id": "research", "status": "verifying"}]
                message = {
                    "role": "assistant",
                    "content": "The specialist candidate awaits independent review.",
                }
            return {"choices": [{"message": message}]}

    provider = DispatchProvider()
    try:
        await Coordinator(store, provider, stream_responses=True).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "verifying"
    assert TaskStore(store).records(run_id)[0]["status"] == "verifying"
    assert any(
        event["event_type"] == "plan.execution_authorized"
        and event["payload"] == {"actor": "coordinator", "source": "plan_run_ready"}
        for event in store.list_events(run_id)
    )
    assert len(store.conversation_messages(run_id)) == 3
    assert not store.unresolved_tool_calls(run_id)
    specialist_deltas = [
        event for event in store.list_events(run_id) if event["event_type"] == "task.model.delta"
    ]
    assert "".join(event["payload"]["text"] for event in specialist_deltas) == (
        "Specialist candidate awaiting review"
    )

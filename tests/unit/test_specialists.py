import asyncio
import base64
import hashlib
import json
import time
from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from xaa.providers.lm_studio import (
    ContextLimitError,
    LMStudioProvider,
    ModelCapabilities,
    ModelInfo,
)
from xaa.providers.streaming import StreamAssemblyError
from xaa.runtime import specialists as specialist_module
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.document_input import render_pdf_scanned_pages
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.task_review import TaskReviewDecision, candidate_evidence, review_candidate
from xaa.storage.memory import MemoryStore
from xaa.storage.runs import RunStore
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.task_workspaces import TaskWorkspaces
from xaa.storage.tasks import TaskStore


def make_blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def make_text_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


async def test_dependency_review_manifest_pages_all_evidence_with_bounded_results(
    workspace_tmp_path, monkeypatch
):
    store = RunStore(workspace_tmp_path / "review-manifest.sqlite3")
    runner = SpecialistRunner(store, LMStudioProvider())
    snapshot = {
        "evidence": [
            {"path": f"{'😀' * 2040}{index}.py", "sha256": "a" * 64} for index in range(17)
        ],
        "workspace_changes": [
            {
                "path": f"changed-{index}.py",
                "base_sha256": "b" * 64,
                "proposed_sha256": "c" * 64,
            }
            for index in range(9)
        ],
        "validation_evidence": [
            {
                "call_id": f"validation-{index}",
                "stdout_artifact_id": f"stdout-{index}",
                "stderr_artifact_id": f"stderr-{index}",
                "succeeded": index == 0,
                "exit_code": 0 if index == 0 else 1,
            }
            for index in range(2)
        ],
    }
    monkeypatch.setattr(
        specialist_module,
        "candidate_evidence",
        lambda *_args, **_kwargs: snapshot,
    )
    pages = []
    offset = 0
    while True:
        page = runner._review_dependency_manifest("run", "dependency", offset=offset, limit=8)
        assert len(json.dumps(page).encode("utf-8")) <= 64 * 1024
        assert len(page["items"]) <= 8
        pages.append(page)
        offset = page["next_offset"]
        if page["eof"]:
            break
    items = [item for page in pages for item in page["items"]]
    assert len(items) == 30
    assert pages[0]["items"] and len(pages[0]["items"]) < 8
    assert all(
        page["offset"] == (pages[index - 1]["next_offset"] if index else 0)
        for index, page in enumerate(pages)
    )
    assert pages[-1]["eof"] is True
    assert {item["kind"] for item in items} == {
        "source_file",
        "changed_artifact",
        "validation_output",
    }
    await runner.provider.close()


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
                "workspace_search",
                "task_delegate_subtasks",
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


async def test_specialist_receives_and_reads_only_its_declared_artifact_inputs(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-input.sqlite3")
    artifact_root = store.path.parent / "artifacts"
    artifact_root.mkdir(exist_ok=True)
    (artifact_root / "source.txt").write_text("assigned source", encoding="utf-8")
    run_id = store.create_run(
        objective="Summarize supplied source",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "coordinator")
    artifact = store.register_artifact(run_id, parent, "source.txt", provenance="user.upload")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="summarize",
                objective="Summarize supplied source",
                input_artifacts=[artifact["id"]],
                expected_outputs=["A concise summary"],
                max_iterations=2,
            )
        ],
    )

    class ArtifactReader(LMStudioProvider):
        async def chat(self, *, model, messages, tools=None):
            if messages[-1]["role"] == "user":
                assignment = json.loads(messages[-1]["content"])
                assert assignment["input_artifact_ids"] == [artifact["id"]]
                assert assignment["expected_outputs"] == ["A concise summary"]
                assert "untrusted" in messages[0]["content"]
                read_tool = next(
                    tool["function"]
                    for tool in tools
                    if tool["function"]["name"] == "task_read_input_artifact"
                )
                assert read_tool["parameters"]["properties"]["artifact_id"]
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "read-source",
                                        "type": "function",
                                        "function": {
                                            "name": "task_read_input_artifact",
                                            "arguments": json.dumps(
                                                {"artifact_id": artifact["id"]}
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            result = json.loads(messages[-1]["content"])
            assert result["content"] == "assigned source"
            assert result["trust"] == "untrusted_user_artifact"
            return {"choices": [{"message": {"role": "assistant", "content": "Source summary"}}]}

    provider = ArtifactReader()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()
    assert statuses == [{"task_id": "summarize", "status": "verifying"}]
    assert json.loads(tasks.records(run_id)[0]["result"]) == {"candidate": "Source summary"}


async def test_specialist_reads_selected_procedure_resources_on_demand(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure-resource.sqlite")
    run_id = store.create_run(
        objective="Use one linked procedure reference",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=32,
    )["id"]
    parent = store.acquire_lease(run_id, "coordinator")
    now = "2026-01-01T00:00:00+00:00"
    content = "Reference text with multibyte characters: café 🧪 " * 3
    content_bytes = content.encode("utf-8")
    pdf_text = "PDF reference read through the bounded resource tool."
    text_pdf_bytes = make_text_pdf(pdf_text)
    extracted_pdf_text = f"[Page 1]\n{pdf_text}"
    image_bytes = b"\x89PNG\r\n\x1a\nfixture-image-data"
    gif_output = BytesIO()
    Image.new("RGB", (2, 2), (220, 10, 20)).save(gif_output, format="GIF")
    gif_bytes = gif_output.getvalue()
    with Image.open(BytesIO(gif_bytes)) as gif_image:
        gif_image.seek(0)
        gif_image.load()
        normalized_output = BytesIO()
        gif_image.convert("RGBA").save(normalized_output, format="PNG", optimize=False)
    converted_gif = normalized_output.getvalue()
    scanned_pdf_bytes = make_blank_pdf()
    rendered_pdf_png = render_pdf_scanned_pages(scanned_pdf_bytes)[0][1]
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO skills(id,name,lifecycle_state,active_version,created_at,updated_at) "
            "VALUES('procedure-resource','procedure-resource','active',1,?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO skill_versions(skill_id,version,content,required_tools,required_permissions,"
            "evaluation_evidence,created_at) VALUES('procedure-resource',1,'Read this reference.','[]','[]','{}',?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO skill_version_assets(skill_id,version,relative_path,media_type,size_bytes,sha256,content) "
            "VALUES('procedure-resource',1,'references/guide.md','text/markdown',?,?,?)",
            (len(content_bytes), hashlib.sha256(content_bytes).hexdigest(), content_bytes),
        )
        connection.execute(
            "INSERT INTO skill_version_assets(skill_id,version,relative_path,media_type,size_bytes,sha256,content) "
            "VALUES('procedure-resource',1,'references/animated.gif','image/gif',?,?,?)",
            (len(gif_bytes), hashlib.sha256(gif_bytes).hexdigest(), gif_bytes),
        )
        connection.execute(
            "INSERT INTO skill_version_assets(skill_id,version,relative_path,media_type,size_bytes,sha256,content) "
            "VALUES('procedure-resource',1,'references/diagram.png','image/png',?,?,?)",
            (len(image_bytes), hashlib.sha256(image_bytes).hexdigest(), image_bytes),
        )
        connection.execute(
            "INSERT INTO skill_version_assets(skill_id,version,relative_path,media_type,size_bytes,sha256,content) "
            "VALUES('procedure-resource',1,'references/scanned.pdf','application/pdf',?,?,?)",
            (
                len(scanned_pdf_bytes),
                hashlib.sha256(scanned_pdf_bytes).hexdigest(),
                scanned_pdf_bytes,
            ),
        )
        connection.execute(
            "INSERT INTO skill_version_assets(skill_id,version,relative_path,media_type,size_bytes,sha256,content) "
            "VALUES('procedure-resource',1,'references/guide.pdf','application/pdf',?,?,?)",
            (len(text_pdf_bytes), hashlib.sha256(text_pdf_bytes).hexdigest(), text_pdf_bytes),
        )
    SpecialistProfileStore(store).set_procedure("researcher", "procedure-resource")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="read-reference",
                objective="Read the linked reference",
                role="researcher",
                max_iterations=16,
            )
        ],
    )

    class ProcedureReader(LMStudioProvider):
        accumulated = ""

        async def models(self):
            return [ModelInfo(id="fixture", capabilities=ModelCapabilities(vision=True))]

        async def chat(self, *, messages, tools=None, **kwargs):
            resource_tool = next(
                tool["function"]
                for tool in tools
                if tool["function"]["name"] == "specialist_procedure_read_resource"
            )
            assert resource_tool["parameters"]["properties"]["path"]
            if messages[-1]["role"] == "user":
                user_content = messages[-1]["content"]
                if isinstance(user_content, list):
                    assert user_content[0]["type"] == "text"
                    image_parts = [part for part in user_content if part["type"] == "image_url"]
                    assert image_parts == [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(converted_gif).decode("ascii")
                            },
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(image_bytes).decode("ascii")
                            },
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(rendered_pdf_png).decode("ascii")
                            },
                        },
                    ]
                    user_text = user_content[0]["text"]
                else:
                    user_text = user_content
                assignment = json.loads(user_text)
                listed = assignment["specialist_procedure_untrusted"]["linked_resources"]
                assert listed == [
                    {
                        "path": "references/animated.gif",
                        "media_type": "image/gif",
                        "sha256": hashlib.sha256(gif_bytes).hexdigest(),
                        "attached_as_image": True,
                        "attached_frame_number": 1,
                    },
                    {
                        "path": "references/diagram.png",
                        "media_type": "image/png",
                        "sha256": hashlib.sha256(image_bytes).hexdigest(),
                        "attached_as_image": True,
                    },
                    {
                        "path": "references/guide.md",
                        "media_type": "text/markdown",
                        "sha256": hashlib.sha256(content_bytes).hexdigest(),
                        "attached_as_image": False,
                    },
                    {
                        "path": "references/guide.pdf",
                        "media_type": "application/pdf",
                        "sha256": hashlib.sha256(text_pdf_bytes).hexdigest(),
                        "attached_as_image": False,
                    },
                    {
                        "path": "references/scanned.pdf",
                        "media_type": "application/pdf",
                        "sha256": hashlib.sha256(scanned_pdf_bytes).hexdigest(),
                        "attached_as_image": True,
                        "attached_page_numbers": [1],
                    },
                ]
                assert all(
                    "content" not in item and "content_base64" not in item for item in listed
                )
                offset = 0
            else:
                page = json.loads(messages[-1]["content"])
                assert page["trust"] == "untrusted_skill_resource"
                self.accumulated += page["content"]
                if page["eof"]:
                    assert self.accumulated == extracted_pdf_text
                    return {
                        "choices": [{"message": {"role": "assistant", "content": "Reference read"}}]
                    }
                offset = page["next_offset"]
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"read-procedure-{offset}",
                                    "type": "function",
                                    "function": {
                                        "name": "specialist_procedure_read_resource",
                                        "arguments": json.dumps(
                                            {
                                                "path": "references/guide.pdf",
                                                "offset": offset,
                                                "limit": 17,
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    provider = ProcedureReader()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 30
        )
    finally:
        await provider.close()
    assert statuses == [{"task_id": "read-reference", "status": "verifying"}]
    events = store.list_events(run_id)
    reads = [event for event in events if event["event_type"] == "task.tool.result"]
    assert len(reads) > 1
    assert all(event["payload"]["result"]["trust"] == "untrusted_skill_resource" for event in reads)


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
    from xaa.runtime import browser, web_fetch, web_search

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
            exposed = next(
                tool["function"]["parameters"]
                for tool in tools or []
                if tool["function"]["name"] == tool_name
            )
            expected_schema = {
                "web_search": web_search.WEB_SEARCH_METADATA["input_schema"],
                "web_fetch": web_fetch.WEB_FETCH_METADATA["input_schema"],
                "browser_run": browser.BROWSER_METADATA["input_schema"],
            }[tool_name]
            assert exposed == expected_schema
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


async def test_provider_failure_blocks_transitive_dependents_without_inference(
    workspace_tmp_path, monkeypatch
):
    store = RunStore(workspace_tmp_path / "specialist-dependency-failure.sqlite3")
    run_id = store.create_run(
        objective="Block dependent work after a specialist fails",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    store.transition(run_id, RunState.PLANNING, owner=parent)
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(id="source", objective="Perform the prerequisite", max_iterations=1),
            TaskDefinition(
                id="review",
                objective="Review the prerequisite result",
                dependencies=("source",),
                max_iterations=1,
            ),
            TaskDefinition(
                id="publish",
                objective="Publish the reviewed result",
                dependencies=("review",),
                max_iterations=1,
            ),
        ],
    )
    store.transition(run_id, RunState.RUNNING, owner=parent)

    class FailingProvider(LMStudioProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise ConnectionError("fixture provider disconnect")

    async def skip_diagnostic(self, run_id, token, model):
        return None

    monkeypatch.setattr(Coordinator, "diagnose_run_tools", skip_diagnostic)
    provider = FailingProvider()
    try:
        await Coordinator(store, provider).continue_plan(run_id, parent)
    finally:
        await provider.close()

    assert provider.calls == 1
    assert {record["local_id"]: record["status"] for record in tasks.records(run_id)} == {
        "source": "failed",
        "review": "blocked",
        "publish": "blocked",
    }
    assert store.get_run(run_id)["state"] == "blocked"
    state_events = [
        event for event in store.list_events(run_id) if event["event_type"] == "run.state_changed"
    ]
    assert state_events[-1]["payload"]["reason"] == "specialist_tasks_blocked"
    records = {record["local_id"]: record for record in tasks.records(run_id)}
    assert records["review"]["blocked_reason"] == "dependency_failed"
    assert records["publish"]["blocked_reason"] == "dependency_failed"
    started = [
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "task.inference.started"
    ]
    assert len(started) == 1
    assert started[0]["payload"]["task_id"] == "source"


@pytest.mark.parametrize(
    ("run_deadline_seconds", "task_seconds", "expected_blocked_reason", "expected_cause"),
    [
        (0.05, 10, "run_time_budget_exhausted", "RunTimeBudgetExceeded"),
        (30, 1, "task_time_budget_exhausted", "TaskTimeBudgetExceeded"),
    ],
)
async def test_specialist_time_budget_expiry_is_reported_as_blocked(
    workspace_tmp_path,
    run_deadline_seconds,
    task_seconds,
    expected_blocked_reason,
    expected_cause,
):
    store = RunStore(workspace_tmp_path / f"specialist-{expected_blocked_reason}.sqlite3")
    run_id = store.create_run(
        objective="Respect specialist wall-clock limits",
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
        [TaskDefinition(id="bounded", objective="Wait for the provider", max_seconds=task_seconds)],
    )

    class SlowProvider(LMStudioProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.cancelled = False

        async def chat(self, **kwargs):
            self.calls += 1
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    provider = SlowProvider()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id,
            parent,
            deadline=time.monotonic() + run_deadline_seconds,
        )
    finally:
        await provider.close()

    assert provider.calls == 1
    assert provider.cancelled
    assert statuses == [{"task_id": "bounded", "status": "blocked"}]
    record = tasks.records(run_id)[0]
    assert record["blocked_reason"] == expected_blocked_reason
    assert json.loads(record["result"])["causes"] == [expected_cause]


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
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO skills(id,name,lifecycle_state,active_version,created_at,updated_at) VALUES('research-procedure','Research procedure','active',1,?,?)",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            'INSERT INTO skill_versions(skill_id,version,content,required_tools,required_permissions,evaluation_evidence,created_at) VALUES(\'research-procedure\',1,\'Use primary sources.\',\'[{"name":"workspace.read_file","version":"1"},{"name":"workspace.write_file","version":"1"},{"name":"web.search","version":"2"}]\',\'["workspace.write"]\',\'{}\',?)',
            ("2026-01-01T00:00:00+00:00",),
        )
    SpecialistProfileStore(store).set_procedure("researcher", "research-procedure")
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
                assignment = json.loads(messages[2]["content"])
                assert assignment["specialist_procedure_untrusted"]["content"] == (
                    "Use primary sources."
                )
                assert (
                    "untrusted reference material"
                    in assignment["specialist_procedure_trust_notice"]
                )
                assert (
                    "workspace.write" in assignment["specialist_procedure_trust_notice"]
                    and "must not be attempted" in assignment["specialist_procedure_trust_notice"]
                )
                assert assignment["unavailable_required_tools"] == [
                    {"name": "workspace.write_file", "version": "1"},
                    {"name": "web.search", "version": "2"},
                ]
                assert "workspace.write_file@1" in assignment["specialist_procedure_trust_notice"]
                assert "web.search@2" in assignment["specialist_procedure_trust_notice"]
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
    readiness = next(
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "task.procedure.readiness_checked"
    )
    assert readiness["payload"]["task_id"] == "research"
    assert readiness["payload"]["procedure_id"] == "research-procedure"
    assert readiness["payload"]["procedure_version"] == 1
    assert readiness["payload"]["required_tool_count"] == 3
    assert readiness["payload"]["unavailable_required_tools"] == [
        {"name": "workspace.write_file", "version": "1"},
        {"name": "web.search", "version": "2"},
    ]
    assert readiness["payload"]["unavailable_required_permissions"] == ["workspace.write"]
    assert readiness["payload"]["ready"] is False
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


async def test_running_specialist_can_delegate_then_resume_after_child_review(
    workspace_tmp_path,
):
    project = workspace_tmp_path / "project"
    project.mkdir()
    (project / "source.txt").write_text("Reviewed source evidence", encoding="utf-8")
    (project / "draft.txt").write_text("before", encoding="utf-8")
    data = workspace_tmp_path / "data"
    data.mkdir()
    store = RunStore(data / "dynamic-specialist.sqlite3")
    run_id = store.create_run(
        objective="Execute a task that discovers independent work",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model="fixture",
        max_iterations=8,
    )["id"]
    parent = store.acquire_lease(run_id, "coordinator")
    store.transition(run_id, RunState.PLANNING, owner=parent)
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO skills(id,name,lifecycle_state,active_version,created_at,updated_at) VALUES('child-procedure','Child procedure','active',1,?,?)",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO skill_versions(skill_id,version,content,required_tools,required_permissions,evaluation_evidence,created_at) VALUES('child-procedure',1,'Report exact source lines.','[]','[]','{}',?)",
            ("2026-01-01T00:00:00+00:00",),
        )
        resource = b"Prefer authoritative references."
        connection.execute(
            "INSERT INTO skill_version_assets(skill_id,version,relative_path,media_type,size_bytes,sha256,content) "
            "VALUES('child-procedure',1,'references/sources.md','text/markdown',?,?,?)",
            (len(resource), hashlib.sha256(resource).hexdigest(), resource),
        )
    SpecialistProfileStore(store).set_procedure("researcher", "child-procedure")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="lead",
                objective="Aggregate independently reviewed source findings",
                acceptance_criteria=["Use the reviewed child result"],
                expected_outputs=["An integrated summary"],
                role="developer",
                permissions=("workspace.read", "workspace.write"),
                max_iterations=5,
                max_seconds=300,
            )
        ],
    )
    store.transition(run_id, RunState.RUNNING, owner=parent)

    class DynamicProvider(LMStudioProvider):
        def __init__(self):
            super().__init__()

        async def chat(self, *, model, messages, tools=None):
            return await self.respond(model=model, messages=messages, tools=tools)

        async def respond(self, *, model, messages, tools=None):
            assignment = json.loads(messages[1]["content"])
            if assignment["objective"] == "Aggregate independently reviewed source findings":
                if assignment["subtask_results_untrusted"]:
                    assert assignment["subtask_result_task_ids"] == ["source-check"]
                    child = assignment["subtask_results_untrusted"][0]
                    assert child["completion"] == "completed_after_owner_review"
                    assert "Reviewed source evidence" in child["candidate_untrusted"]
                    changes = TaskWorkspaces(store).changes(run_id, "lead")
                    assert [item["path"] for item in changes] == ["draft.txt"]
                    message = {"role": "assistant", "content": "Integrated summary"}
                else:
                    previous_call = None
                    if messages[-1]["role"] == "tool":
                        previous_result = json.loads(messages[-1]["content"])
                        if previous_result.get("text") == "before":
                            previous_call = "workspace_read_file"
                        elif previous_result.get("bytes_written") is not None:
                            previous_call = "workspace_write_file"
                        else:
                            raise AssertionError(previous_result)
                    if previous_call == "workspace_read_file":
                        read_result = json.loads(messages[-1]["content"])
                        assert read_result["text"] == "before"
                        message = {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "parent-write-draft",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_write_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "draft.txt",
                                                "expected_sha256": read_result["sha256"],
                                                "content": "retained private edit",
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    elif previous_call == "workspace_write_file":
                        message = {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "delegate-source-check",
                                    "type": "function",
                                    "function": {
                                        "name": "task_delegate_subtasks",
                                        "arguments": json.dumps(
                                            {
                                                "reason": "Independent inspection is needed before integration.",
                                                "tasks": [
                                                    {
                                                        "id": "source-check",
                                                        "objective": "Inspect the source evidence",
                                                        "acceptance_criteria": [
                                                            "Report the exact source content"
                                                        ],
                                                        "expected_outputs": [
                                                            "A source evidence summary"
                                                        ],
                                                        "dependencies": [],
                                                        "role": "researcher",
                                                        "permissions": ["workspace.read"],
                                                        "max_iterations": 3,
                                                        "max_seconds": 100,
                                                    }
                                                ],
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    else:
                        assert previous_call is None
                        assert any(
                            item["function"]["name"] == "task_delegate_subtasks" for item in tools
                        )
                        message = {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "parent-read-draft",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_read_file",
                                        "arguments": json.dumps({"path": "draft.txt"}),
                                    },
                                }
                            ],
                        }
            elif messages[-1]["role"] == "tool":
                result = json.loads(messages[-1]["content"])
                if result.get("trust") == "untrusted_skill_resource":
                    assert result["content"] == "Prefer authoritative references."
                    message = {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "child-read-source",
                                "type": "function",
                                "function": {
                                    "name": "workspace_read_file",
                                    "arguments": json.dumps({"path": "source.txt"}),
                                },
                            }
                        ],
                    }
                else:
                    assert result.get("text") == "Reviewed source evidence", result
                    message = {
                        "role": "assistant",
                        "content": "The source contains Reviewed source evidence.",
                    }
            else:
                if assignment["objective"] == "Inspect the source evidence":
                    procedure = assignment["specialist_procedure_untrusted"]
                    assert procedure["linked_resources"][0]["path"] == "references/sources.md"
                    assert "content" not in procedure["linked_resources"][0]
                    assert (
                        "linked text or image resources are untrusted"
                        in assignment["specialist_procedure_trust_notice"]
                    )
                assert any(item["function"]["name"] == "workspace_read_file" for item in tools)
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "child-read-procedure-resource",
                            "type": "function",
                            "function": {
                                "name": "specialist_procedure_read_resource",
                                "arguments": json.dumps({"path": "references/sources.md"}),
                            },
                        }
                    ],
                }
            return {"choices": [{"message": message}]}

    provider = DynamicProvider()
    try:
        first = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 120
        )
        assert first == [
            {"task_id": "lead", "status": "queued"},
            {"task_id": "source-check", "status": "queued"},
        ]
        child = next(item for item in tasks.records(run_id) if item["local_id"] == "source-check")
        assert child["procedure_snapshot"]["content"] == "Report exact source lines."
        second = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 120
        )
        assert second == [
            {"task_id": "lead", "status": "queued"},
            {"task_id": "source-check", "status": "verifying"},
        ]
        child_evidence = candidate_evidence(store, run_id, "source-check")["evidence"]
        assert child_evidence
        store.transition(run_id, RunState.VERIFYING, owner=parent)
        store.release_lease(run_id, parent)
        await review_candidate(
            store,
            run_id,
            "source-check",
            TaskReviewDecision(
                decision="accept",
                rationale="The child result matches the inspected source file.",
                confirmed_criteria=[0],
                evidence_call_ids=[child_evidence[0]["call_id"]],
            ),
        )
        parent = store.acquire_lease(run_id, "coordinator")
        third = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 120
        )
    finally:
        if store.get_run(run_id)["lease_owner"] == parent:
            store.release_lease(run_id, parent)
        await provider.close()

    assert third == [
        {"task_id": "lead", "status": "verifying"},
        {"task_id": "source-check", "status": "completed"},
    ]
    rows = {item["local_id"]: item for item in tasks.records(run_id)}
    assert json.loads(rows["lead"]["result"]) == {"candidate": "Integrated summary"}
    assert rows["lead"]["status"] == "verifying"
    assert rows["source-check"]["status"] == "completed"
    assert not store.unresolved_tool_calls(run_id)

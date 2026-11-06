from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.interfaces import Client
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from xaa.acp_adapter import XaaAcpAgent, validate_local_api_url
from xaa.cli import _resolve_api_token, _resolve_api_url
from xaa.credentials import API_LAUNCH_TOKEN_PROFILE, API_LAUNCH_URL_PROFILE
from xaa.version import APP_VERSION

TOKEN = "acp-test-token-that-is-longer-than-thirty-two-bytes"
REPOSITORY = Path(__file__).resolve().parents[2]


def test_api_url_must_be_loopback_and_uncredentialed() -> None:
    assert validate_local_api_url("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"
    for value in (
        "https://127.0.0.1:8765",
        "http://localhost:8765",
        "http://192.168.1.2:8765",
        "http://user:pass@127.0.0.1:8765",
        "http://127.0.0.1:8765/?token=secret",
        "http://127.0.0.1:0",
        "http://127.0.0.1:invalid",
        "http://127.0.0.1",
    ):
        with pytest.raises(ValueError):
            validate_local_api_url(value)


def test_acp_cli_prefers_environment_token_then_uses_launch_vault(monkeypatch: Any) -> None:
    class FakeCredentialStore:
        def get(self, profile: str) -> str:
            if profile == API_LAUNCH_TOKEN_PROFILE:
                return "vault-token-that-is-longer-than-thirty-two-bytes"
            assert profile == API_LAUNCH_URL_PROFILE
            return "http://127.0.0.1:45678"

    monkeypatch.setattr("xaa.cli.CredentialStore", FakeCredentialStore)
    assert _resolve_api_token("acp", TOKEN) == TOKEN
    assert _resolve_api_token("acp", "") == "vault-token-that-is-longer-than-thirty-two-bytes"
    assert _resolve_api_token("models", "") == ""
    assert _resolve_api_url("acp", None, None) == "http://127.0.0.1:45678"
    assert _resolve_api_url("acp", "http://127.0.0.1:1234", None) == "http://127.0.0.1:1234"
    assert _resolve_api_url("models", None, None) == "http://127.0.0.1:8765"


class UpdateSink:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str]] = []
        self.tool_updates: list[Any] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if getattr(update, "tool_call_id", None) is not None:
            self.tool_updates.append(update)
        content = getattr(update, "content", None)
        text = getattr(content, "text", "")
        if isinstance(text, str) and text:
            self.updates.append((session_id, text))


def _api_fixture(
    workspace: Path, *, initial_state: str = "completed"
) -> tuple[FastAPI, dict[str, Any]]:
    app = FastAPI()
    state: dict[str, Any] = {
        "runs": [],
        "run_states": {},
        "sessions": {},
        "requests": [],
        "cancellations": [],
        "run_events": {},
        "next_state": initial_state,
    }

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        return await call_next(request)

    @app.get("/chat/sessions/{session_id}")
    async def get_session(session_id: str) -> Any:
        value = state["sessions"].get(session_id)
        return (
            JSONResponse(status_code=404, content={"detail": "not found"})
            if value is None
            else value
        )

    @app.get("/chat/acp-sessions")
    async def list_sessions(
        model: str, workspace: str, mode: str, after: str = "", limit: int = 50
    ) -> list[dict[str, str]]:
        return [
            {"id": key, "title": f"ACP {key}", "updated_at": "2026-11-06T00:00:00Z"}
            for key, value in sorted(state["sessions"].items())
            if key > after
            and value["model"] == model
            and value["workspace"] == workspace
            and value["mode"] == mode
        ][:limit]

    @app.post("/runs")
    async def create_run(request: Request) -> dict[str, str]:
        payload = await request.json()
        state["requests"].append(payload)
        run_id = f"run-{len(state['runs']) + 1}"
        state["runs"].append(run_id)
        state["run_states"][run_id] = state["next_state"]
        return {"id": run_id}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, str]:
        return {"state": state["run_states"][run_id]}

    @app.get("/runs/{run_id}/events")
    async def get_events(run_id: str, after: int = 0) -> list[dict[str, Any]]:
        events = state["run_events"].get(run_id) or [
            {
                "sequence": 1,
                "event_type": "model.message",
                "payload": {"message": {"content": f"Fixture answer for {run_id}"}},
            }
        ]
        return [event for event in events if event["sequence"] > after]

    @app.get("/runs/{run_id}/tools/{call_id}/result")
    async def get_tool_result(run_id: str, call_id: str) -> dict[str, str | None]:
        if call_id == "call-public":
            return {"result": '{"ok": true, "contents": "fixture file"}'}
        return {"result": None}

    @app.post("/chat/sessions/{session_id}/turn")
    async def append_turn(session_id: str, request: Request) -> dict[str, Any]:
        turn = await request.json()
        previous = state["sessions"].get(session_id, {}).get("messages", [])
        messages = [
            *previous,
            {"role": "user", "content": turn["user_message"], "run_id": turn["run_id"]},
            {
                "role": "assistant",
                "content": turn["assistant_message"],
                "run_id": turn["run_id"],
            },
        ]
        state["sessions"][session_id] = {
            "session_id": session_id,
            "model": turn["model"],
            "workspace": str(workspace),
            "mode": turn["mode"],
            "messages": messages,
        }
        return state["sessions"][session_id]

    @app.delete("/chat/acp-sessions/{session_id}", status_code=204, response_class=Response)
    async def delete_acp_session(session_id: str, request: Request) -> Response:
        params = request.query_params
        value = state["sessions"].get(session_id)
        if value is not None and all(
            value.get(key) == params.get(key) for key in ("model", "workspace", "mode")
        ):
            state["sessions"].pop(session_id)
        return Response(status_code=204)

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, str]:
        state["run_states"][run_id] = "cancelled"
        state["cancellations"].append(run_id)
        return {"state": "cancelled"}

    return app, state


@pytest.mark.asyncio
async def test_acp_returns_candidate_while_run_awaits_independent_review(tmp_path: Path) -> None:
    app, state = _api_fixture(tmp_path.resolve(), initial_state="verifying")
    api_url = "http://127.0.0.1:43210"
    client = httpx.AsyncClient(
        base_url=api_url,
        headers={"Authorization": f"Bearer {TOKEN}"},
        transport=httpx.ASGITransport(app=app),
    )
    sink = UpdateSink()
    agent = XaaAcpAgent(
        api_url=api_url,
        api_token=TOKEN,
        model="fixture-model",
        workspace=tmp_path.resolve(),
        mode="observe",
        client=client,
    )
    agent.on_connect(cast(Client, sink))
    try:
        session = await agent.new_session(cwd=str(tmp_path.resolve()), mcp_servers=[])
        response = await agent.prompt(
            prompt=[{"text": "Read the project status."}], session_id=session.session_id
        )
    finally:
        await client.aclose()

    assert response.stop_reason == "end_turn"
    candidate_response = (
        "Fixture answer for run-1\n\nThis result is a candidate awaiting independent "
        "review in Xyst AI Agent. Review it in the desktop app; it has not been marked complete."
    )
    assert sink.updates == [
        (
            session.session_id,
            candidate_response,
        )
    ]
    assert state["sessions"] == {}


@pytest.mark.asyncio
async def test_acp_initialize_advertises_stable_session_delete(tmp_path: Path) -> None:
    agent = XaaAcpAgent(
        api_url="http://127.0.0.1:43210",
        api_token=TOKEN,
        model="fixture-model",
        workspace=tmp_path.resolve(),
        mode="observe",
        client=httpx.AsyncClient(
            base_url="http://127.0.0.1:43210",
            transport=httpx.MockTransport(lambda _: httpx.Response(204)),
        ),
    )
    try:
        response = await agent.initialize(protocol_version=PROTOCOL_VERSION)
        encoded = response.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_unset=True
        )
        assert encoded["agentCapabilities"]["sessionCapabilities"]["delete"] == {}
    finally:
        await agent.aclose()


@pytest.mark.asyncio
async def test_acp_stdio_delegates_two_turns_to_authenticated_run_api(tmp_path: Path) -> None:
    app, state = _api_fixture(tmp_path.resolve())
    state["run_events"]["run-1"] = [
        {
            "sequence": 1,
            "event_type": "tool.intended",
            "payload": {"call_id": "call-public", "tool": "workspace.read_file"},
        },
        {
            "sequence": 2,
            "event_type": "tool.executing",
            "payload": {"call_id": "call-public"},
        },
        {
            "sequence": 3,
            "event_type": "tool.completed",
            "payload": {"call_id": "call-public"},
        },
        {
            "sequence": 4,
            "event_type": "tool.intended",
            "payload": {
                "call_id": "call-internal",
                "tool": "internal.check",
                "internal": True,
            },
        },
        {
            "sequence": 5,
            "event_type": "tool.executing",
            "payload": {"call_id": "call-internal"},
        },
        {
            "sequence": 6,
            "event_type": "tool.completed",
            "payload": {"call_id": "call-internal", "internal": True},
        },
        {
            "sequence": 7,
            "event_type": "model.message",
            "payload": {"message": {"content": "Fixture answer for run-1"}},
        },
    ]
    state["run_events"]["run-2"] = [
        {
            "sequence": 1,
            "event_type": "tool.intended",
            "payload": {"call_id": "call-failed", "tool": "workspace.search"},
        },
        {
            "sequence": 2,
            "event_type": "tool.executing",
            "payload": {"call_id": "call-failed"},
        },
        {
            "sequence": 3,
            "event_type": "tool.failed",
            "payload": {"call_id": "call-failed"},
        },
        {
            "sequence": 4,
            "event_type": "model.message",
            "payload": {"message": {"content": "Fixture answer for run-2"}},
        },
    ]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.setblocking(False)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.02)
    assert server.started

    env = {**os.environ, "XAA_API_TOKEN": TOKEN}
    sink = UpdateSink()
    try:
        async with spawn_agent_process(
            sink,
            sys.executable,
            "-m",
            "xaa.cli",
            "--api-url",
            f"http://127.0.0.1:{port}",
            "acp",
            "--model",
            "fixture-model",
            "--workspace",
            str(tmp_path.resolve()),
            env=env,
            cwd=REPOSITORY,
        ) as (connection, _process):
            initialized = await connection.initialize(protocol_version=PROTOCOL_VERSION)
            assert initialized.protocol_version == PROTOCOL_VERSION
            assert initialized.agent_info is not None
            assert initialized.agent_info.version == APP_VERSION
            session = await connection.new_session(cwd=str(tmp_path.resolve()), mcp_servers=[])
            first = await connection.prompt(
                session_id=session.session_id, prompt=[text_block("Read the project status.")]
            )
            second = await connection.prompt(
                session_id=session.session_id, prompt=[text_block("Now summarize that again.")]
            )
            closed = await connection.close_session(session_id=session.session_id)
            assert closed is not None

        replay_sink = UpdateSink()
        async with spawn_agent_process(
            replay_sink,
            sys.executable,
            "-m",
            "xaa.cli",
            "--api-url",
            f"http://127.0.0.1:{port}",
            "acp",
            "--model",
            "fixture-model",
            "--workspace",
            str(tmp_path.resolve()),
            env=env,
            cwd=REPOSITORY,
        ) as (connection, _process):
            initialized = await connection.initialize(protocol_version=PROTOCOL_VERSION)
            assert initialized.agent_capabilities is not None
            assert initialized.agent_capabilities.load_session is True
            listed = await connection.list_sessions(cwd=str(tmp_path.resolve()))
            assert [item.session_id for item in listed.sessions] == [session.session_id]
            await connection.load_session(
                cwd=str(tmp_path.resolve()), session_id=session.session_id, mcp_servers=[]
            )
            third = await connection.prompt(
                session_id=session.session_id, prompt=[text_block("Continue after restart.")]
            )
            saved_messages = state["sessions"][session.session_id]["messages"]
            assert len(saved_messages) == 6
            deleted = await connection._conn.send_request(
                "session/delete", {"sessionId": session.session_id}
            )
            assert deleted == {}
            listed_after_delete = await connection.list_sessions(cwd=str(tmp_path.resolve()))
            assert listed_after_delete.sessions == []
            state["next_state"] = "running"
            active_session = await connection.new_session(
                cwd=str(tmp_path.resolve()), mcp_servers=[]
            )
            active_prompt = asyncio.create_task(
                connection.prompt(
                    session_id=active_session.session_id,
                    prompt=[text_block("This prompt will be cancelled by session deletion.")],
                )
            )
            for _ in range(100):
                if len(state["runs"]) == 4:
                    break
                await asyncio.sleep(0.01)
            assert len(state["runs"]) == 4
            active_delete = await connection._conn.send_request(
                "session/delete", {"sessionId": active_session.session_id}
            )
            assert active_delete == {}
            cancelled_prompt = await asyncio.wait_for(active_prompt, timeout=5)
            assert cancelled_prompt.stop_reason == "cancelled"
        assert third.stop_reason == "end_turn"
        assert [text for _, text in replay_sink.updates] == [
            "Read the project status.",
            "Fixture answer for run-1",
            "Now summarize that again.",
            "Fixture answer for run-2",
            "Fixture answer for run-3",
        ]

        assert first.stop_reason == "end_turn"
        assert second.stop_reason == "end_turn"
        assert len(state["runs"]) == 4
        assert session.session_id not in state["sessions"]
        assert active_session.session_id not in state["sessions"]
        assert state["cancellations"] == ["run-4"]
        assert len(state["requests"]) == 4
        assert all(item["model"] == "fixture-model" for item in state["requests"])
        assert all(item["workspace"] == str(tmp_path.resolve()) for item in state["requests"])
        assert all(item["mode"] == "observe" for item in state["requests"])
        assert "Read the project status." in state["requests"][1]["objective"]
        assert "Now summarize that again." in state["requests"][1]["objective"]
        assert "Now summarize that again." in state["requests"][2]["objective"]
        assert saved_messages[-2]["content"] == "Continue after restart."
        assert [text for _, text in sink.updates] == [
            "Fixture answer for run-1",
            "Fixture answer for run-2",
        ]
        assert [update.session_update for update in sink.tool_updates] == [
            "tool_call",
            "tool_call_update",
            "tool_call_update",
            "tool_call",
            "tool_call_update",
            "tool_call_update",
        ]
        assert [update.status for update in sink.tool_updates] == [
            "pending",
            "in_progress",
            "completed",
            "pending",
            "in_progress",
            "failed",
        ]
        assert len({update.tool_call_id for update in sink.tool_updates[:3]}) == 1
        assert len({update.tool_call_id for update in sink.tool_updates[3:]}) == 1
        assert sink.tool_updates[0].tool_call_id != sink.tool_updates[3].tool_call_id
        assert all(
            update.raw_input is None and update.raw_output is None for update in sink.tool_updates
        )
        completed_content = sink.tool_updates[2].content
        assert completed_content is not None
        assert completed_content[0].content.text == '{"ok": true, "contents": "fixture file"}'
        assert all("internal" not in update.title for update in sink.tool_updates)
    finally:
        server.should_exit = True
        await server_task
        sock.close()


@pytest.mark.asyncio
async def test_acp_cancel_propagates_to_durable_run(tmp_path: Path) -> None:
    app, state = _api_fixture(tmp_path.resolve(), initial_state="running")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.setblocking(False)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.02)
    assert server.started

    env = {**os.environ, "XAA_API_TOKEN": TOKEN}
    sink = UpdateSink()
    try:
        async with spawn_agent_process(
            sink,
            sys.executable,
            "-m",
            "xaa.cli",
            "--api-url",
            f"http://127.0.0.1:{port}",
            "acp",
            "--model",
            "fixture-model",
            "--workspace",
            str(tmp_path.resolve()),
            env=env,
            cwd=REPOSITORY,
        ) as (connection, _process):
            await connection.initialize(protocol_version=PROTOCOL_VERSION)
            session = await connection.new_session(cwd=str(tmp_path.resolve()), mcp_servers=[])
            prompt_task = asyncio.create_task(
                connection.prompt(
                    session_id=session.session_id,
                    prompt=[text_block("Start a long running task.")],
                )
            )
            for _ in range(100):
                if state["runs"]:
                    break
                await asyncio.sleep(0.02)
            assert state["runs"]
            await connection.cancel(session_id=session.session_id)
            response = await asyncio.wait_for(prompt_task, timeout=5)

        assert response.stop_reason == "cancelled"
        assert state["cancellations"] == ["run-1"]
        assert state["sessions"] == {}
        assert sink.updates == []
    finally:
        server.should_exit = True
        await server_task
        sock.close()

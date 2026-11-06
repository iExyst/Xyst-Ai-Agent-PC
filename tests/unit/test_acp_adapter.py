from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from xaa.acp_adapter import validate_local_api_url
from xaa.cli import _resolve_api_token, _resolve_api_url
from xaa.credentials import API_LAUNCH_TOKEN_PROFILE, API_LAUNCH_URL_PROFILE

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

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        content = getattr(update, "content", None)
        text = getattr(content, "text", "")
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

    @app.post("/runs")
    async def create_run(request: Request) -> dict[str, str]:
        payload = await request.json()
        state["requests"].append(payload)
        run_id = f"run-{len(state['runs']) + 1}"
        state["runs"].append(run_id)
        state["run_states"][run_id] = initial_state
        return {"id": run_id}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, str]:
        return {"state": state["run_states"][run_id]}

    @app.get("/runs/{run_id}/events")
    async def get_events(run_id: str) -> list[dict[str, Any]]:
        return [
            {
                "sequence": 1,
                "event_type": "model.message",
                "payload": {"message": {"content": f"Fixture answer for {run_id}"}},
            }
        ]

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

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, str]:
        state["run_states"][run_id] = "cancelled"
        state["cancellations"].append(run_id)
        return {"state": "cancelled"}

    return app, state


@pytest.mark.asyncio
async def test_acp_stdio_delegates_two_turns_to_authenticated_run_api(tmp_path: Path) -> None:
    app, state = _api_fixture(tmp_path.resolve())
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
            session = await connection.new_session(cwd=str(tmp_path.resolve()), mcp_servers=[])
            first = await connection.prompt(
                session_id=session.session_id, prompt=[text_block("Read the project status.")]
            )
            second = await connection.prompt(
                session_id=session.session_id, prompt=[text_block("Now summarize that again.")]
            )

        assert first.stop_reason == "end_turn"
        assert second.stop_reason == "end_turn"
        assert len(state["runs"]) == 2
        assert len(state["requests"]) == 2
        assert all(item["model"] == "fixture-model" for item in state["requests"])
        assert all(item["workspace"] == str(tmp_path.resolve()) for item in state["requests"])
        assert all(item["mode"] == "observe" for item in state["requests"])
        assert "Read the project status." in state["requests"][1]["objective"]
        assert "Now summarize that again." in state["requests"][1]["objective"]
        assert state["sessions"][session.session_id]["messages"][-2]["content"] == (
            "Now summarize that again."
        )
        assert [text for _, text in sink.updates] == [
            "Fixture answer for run-1",
            "Fixture answer for run-2",
        ]
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

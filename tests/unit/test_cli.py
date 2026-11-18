import json
import sys
from pathlib import Path
from typing import Self

import httpx
from xaa import cli


def test_cli_exports_run_trajectory_to_requested_file(
    workspace_tmp_path: Path, monkeypatch, capsys
) -> None:
    body = b'{"format":"xaa-trajectory","events":[]}'
    calls: list[tuple[str, dict[str, str]]] = []

    class Response:
        content = body

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: int) -> None:
            assert base_url == "http://127.0.0.1:8765"
            assert headers == {"Authorization": f"Bearer {'x' * 32}"}
            assert timeout == 30

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, path: str, params: dict[str, object] | None = None) -> Response:
            calls.append((path, {"method": "GET"}))
            return Response()

    monkeypatch.setattr(cli.httpx, "Client", Client)
    monkeypatch.setenv("XAA_API_TOKEN", "x" * 32)
    output = workspace_tmp_path / "run.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["xaa", "export", "run/id", "--output", str(output)],
    )

    cli.main()

    assert output.read_bytes() == body
    assert calls == [("/runs/run%2Fid/export", {"method": "GET"})]
    assert str(output) in capsys.readouterr().out


def test_cli_history_uses_authenticated_conversation_search(monkeypatch, capsys) -> None:
    calls: list[tuple[str, dict[str, object] | None]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"items": [{"id": "run-1"}], "next_cursor": "opaque"}

    class Client:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: int) -> None:
            assert headers["Authorization"] == f"Bearer {'y' * 32}"

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, path: str, params: dict[str, object] | None = None) -> Response:
            calls.append((path, params))
            return Response()

    monkeypatch.setattr(cli.httpx, "Client", Client)
    monkeypatch.setenv("XAA_API_TOKEN", "y" * 32)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xaa",
            "history",
            "--query",
            "deployment phrase",
            "--scope",
            "conversation",
            "--limit",
            "7",
            "--before",
            "cursor",
        ],
    )

    cli.main()

    assert calls == [
        (
            "/runs",
            {
                "q": "deployment phrase",
                "scope": "conversation",
                "limit": 7,
                "before": "cursor",
            },
        )
    ]
    assert '"next_cursor": "opaque"' in capsys.readouterr().out


def test_cli_manages_shared_chat_sessions(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str, object | None]] = []

    class Response:
        def __init__(self, body: object = None) -> None:
            self.body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.body

    class Client:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: int) -> None:
            assert headers["Authorization"] == f"Bearer {'s' * 32}"

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, path: str) -> Response:
            calls.append(("GET", path, None))
            return Response([{"session_id": "project", "title": "Project"}])

        def patch(self, path: str, json: dict[str, object]) -> Response:
            calls.append(("PATCH", path, json))
            return Response({"session_id": "project", "title": json["title"]})

        def delete(self, path: str) -> Response:
            calls.append(("DELETE", path, None))
            return Response()

    monkeypatch.setattr(cli.httpx, "Client", Client)
    monkeypatch.setenv("XAA_API_TOKEN", "s" * 32)
    for argv in (
        ["xaa", "sessions", "list"],
        ["xaa", "sessions", "rename", "project", "Release notes"],
        ["xaa", "sessions", "delete", "project", "--yes"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        cli.main()

    assert calls == [
        ("GET", "/chat/sessions", None),
        ("PATCH", "/chat/sessions/project", {"title": "Release notes"}),
        ("DELETE", "/chat/sessions/project", None),
    ]
    output = capsys.readouterr().out
    assert '"session_id": "project"' in output
    assert "Deleted saved session project." in output


def test_cli_requires_explicit_confirmation_before_deleting_a_session(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["xaa", "sessions", "delete", "project"])
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Session deletion did not require explicit confirmation")


def test_cli_chat_carries_context_and_reset_starts_fresh_session(monkeypatch, capsys) -> None:
    objectives: list[str] = []
    answers = ["First answer", "Second answer", "Fresh answer", "Fourth answer"]
    stream_attempts: dict[str, int] = {}
    session: dict[str, object] | None = None

    class Response:
        def __init__(self, body: object, status_code: int = 200) -> None:
            self.body = body
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "not found",
                    request=httpx.Request("GET", "http://127.0.0.1"),
                    response=httpx.Response(self.status_code),
                )

        def json(self) -> object:
            return self.body

    class StreamResponse(Response):
        def __init__(self, lines: list[str]) -> None:
            super().__init__(None)
            self.lines = lines

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_lines(self):
            yield from self.lines

    class Client:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: int) -> None:
            assert base_url == "http://127.0.0.1:8765"
            assert headers["Authorization"] == f"Bearer {'z' * 32}"

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, path: str, json: dict[str, object] | None = None) -> Response:
            assert json is not None
            nonlocal session
            if path == "/runs":
                objectives.append(str(json["objective"]))
                return Response({"id": f"run-{len(objectives)}"})
            assert path == "/chat/sessions/default/turn"
            old_messages = (
                session["messages"]
                if session is not None and isinstance(session.get("messages"), list)
                else []
            )
            session = {
                "session_id": "default",
                "model": json["model"],
                "workspace": json["workspace"],
                "mode": json["mode"],
                "messages": [
                    *old_messages,
                    {"role": "user", "content": json["user_message"], "run_id": json["run_id"]},
                    {
                        "role": "assistant",
                        "content": json["assistant_message"],
                        "run_id": json["run_id"],
                    },
                ],
            }
            return Response(session)

        def get(self, path: str, params: dict[str, object] | None = None) -> Response:
            if path == "/chat/sessions/default":
                return Response(session, 404) if session is None else Response(session)
            return Response({"state": "verifying"})

        def stream(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, object],
            timeout: None,
        ) -> StreamResponse:
            assert method == "GET"
            assert path.endswith("/events/stream")
            assert timeout is None
            run_id = path.split("/")[2]
            index = int(run_id.split("-")[1]) - 1
            attempt = stream_attempts.get(run_id, 0) + 1
            stream_attempts[run_id] = attempt
            cursor = int(params["after"])
            if index == 0 and attempt == 1:
                return StreamResponse(
                    [
                        "id: 1",
                        "event: run_event",
                        'data: {"sequence":1,"event_type":"model.delta","payload":{"inference_id":"inference-1","text":"First "}}',
                        "",
                    ]
                )
            first_sequence = cursor + 1
            answer = answers[index]
            delta = answer[len("First ") :] if index == 0 and cursor == 1 else answer
            events = [
                {
                    "sequence": first_sequence,
                    "event_type": "model.delta",
                    "payload": {"inference_id": f"inference-{index + 1}", "text": delta},
                },
                {
                    "sequence": first_sequence + 1,
                    "event_type": "model.message",
                    "payload": {
                        "inference_id": f"inference-{index + 1}",
                        "message": {"role": "assistant", "content": answer},
                    },
                },
                {
                    "sequence": first_sequence + 2,
                    "event_type": "run.state_changed",
                    "payload": {"to": "verifying"},
                },
            ]
            lines: list[str] = []
            for event in events:
                lines.extend(
                    [
                        f"id: {event['sequence']}",
                        "event: run_event",
                        f"data: {json.dumps(event)}",
                        "",
                    ]
                )
            return StreamResponse(lines)

        def delete(self, path: str) -> Response:
            assert path == "/chat/sessions/default"
            nonlocal session
            session = None
            return Response(None, 204)

    prompts = iter(["First question", "Second question", "/reset", "Third question", "/quit"])
    monkeypatch.setattr(cli.httpx, "Client", Client)
    monkeypatch.setenv("XAA_API_TOKEN", "z" * 32)
    monkeypatch.setattr(sys, "argv", ["xaa", "chat", "--model", "local/model"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(prompts))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    cli.main()

    assert len(objectives) == 3
    assert "First question" in objectives[1]
    assert "First answer" in objectives[1]
    assert "First question" not in objectives[2]
    assert "Second question" not in objectives[2]
    output = capsys.readouterr().out
    assert "Conversation context cleared" in output
    assert "agent> Fresh answer" in output
    assert output.count("First answer") == 1
    assert "First First answer" not in output
    assert stream_attempts["run-1"] == 2

    next_prompts = iter(["Fourth question", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(next_prompts))
    cli.main()
    assert "Third question" in objectives[3]
    assert "Fresh answer" in objectives[3]


def test_cli_chat_rejects_mismatched_event_id_and_reconnects(monkeypatch, capsys) -> None:
    cursors: list[int] = []

    class StreamResponse:
        def __init__(self, lines: list[str]) -> None:
            self.lines = lines

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            yield from self.lines

    class Client:
        attempts = 0

        def stream(
            self,
            _method: str,
            _path: str,
            *,
            params: dict[str, object],
            timeout: float | None,
        ) -> StreamResponse:
            assert timeout is None
            cursor = int(params["after"])
            cursors.append(cursor)
            self.attempts += 1
            if self.attempts == 1:
                event = {
                    "sequence": 1,
                    "event_type": "model.delta",
                    "payload": {"inference_id": "i", "text": "incorrect"},
                }
                lines = ["id: 9", "event: run_event", f"data: {json.dumps(event)}", ""]
            else:
                lines = []
                events = [
                    {
                        "sequence": 1,
                        "event_type": "model.delta",
                        "payload": {"inference_id": "i", "text": "Correct answer"},
                    },
                    {
                        "sequence": 2,
                        "event_type": "model.message",
                        "payload": {
                            "inference_id": "i",
                            "message": {"role": "assistant", "content": "Correct answer"},
                        },
                    },
                    {
                        "sequence": 3,
                        "event_type": "run.state_changed",
                        "payload": {"to": "verifying"},
                    },
                ]
                for event in events:
                    lines.extend(
                        [
                            f"id: {event['sequence']}",
                            "event: run_event",
                            f"data: {json.dumps(event)}",
                            "",
                        ]
                    )
            return StreamResponse(lines)

    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    state, answer = cli._stream_chat_run(Client(), "/runs/fixture")

    assert cursors == [0, 0]
    assert state == "verifying"
    assert answer == "Correct answer"
    output = capsys.readouterr().out
    assert "Correct answer" in output
    assert "incorrect" not in output


def test_cli_chat_interrupt_requests_run_cancellation(monkeypatch, capsys) -> None:
    posts: list[str] = []

    class Response:
        def __init__(self, body: object, status_code: int = 200) -> None:
            self.body = body
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "not found",
                    request=httpx.Request("GET", "http://127.0.0.1"),
                    response=httpx.Response(self.status_code),
                )

        def json(self) -> object:
            return self.body

    class Client:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: int) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, path: str, json: dict[str, object] | None = None) -> Response:
            posts.append(path)
            return Response({"id": "cancel-me"})

        def get(self, path: str, params: dict[str, object] | None = None) -> Response:
            if path == "/chat/sessions/default":
                return Response(None, 404)
            return Response({"state": "running"})

        def stream(self, *_args: object, **_kwargs: object):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.httpx, "Client", Client)
    monkeypatch.setenv("XAA_API_TOKEN", "c" * 32)
    monkeypatch.setattr(sys, "argv", ["xaa", "chat", "--model", "local/model"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "Interrupt this run")

    cli.main()

    assert posts == ["/runs", "/runs/cancel-me/cancel"]
    assert "Cancellation requested for run cancel-me" in capsys.readouterr().out

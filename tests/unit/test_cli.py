import sys
from pathlib import Path
from typing import Self

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


def test_cli_chat_carries_context_and_reset_starts_fresh_session(monkeypatch, capsys) -> None:
    objectives: list[str] = []
    answers = ["First answer", "Second answer", "Fresh answer"]

    class Response:
        def __init__(self, body: object) -> None:
            self.body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.body

    class Client:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: int) -> None:
            assert base_url == "http://127.0.0.1:8765"
            assert headers["Authorization"] == f"Bearer {'z' * 32}"

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, path: str, json: dict[str, object] | None = None) -> Response:
            assert path == "/runs"
            assert json is not None
            objectives.append(str(json["objective"]))
            return Response({"id": f"run-{len(objectives)}"})

        def get(self, path: str, params: dict[str, object] | None = None) -> Response:
            if path.endswith("/events"):
                index = int(path.split("/")[2].split("-")[1]) - 1
                return Response(
                    [
                        {
                            "event_type": "model.message",
                            "payload": {
                                "message": {"role": "assistant", "content": answers[index]}
                            },
                        }
                    ]
                )
            return Response({"state": "verifying"})

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
    assert "agent [verifying]> Fresh answer" in output


def test_cli_chat_interrupt_requests_run_cancellation(monkeypatch, capsys) -> None:
    posts: list[str] = []

    class Response:
        def __init__(self, body: object) -> None:
            self.body = body

        def raise_for_status(self) -> None:
            return None

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
            if path.endswith("/events"):
                return Response([])
            return Response({"state": "running"})

    monkeypatch.setattr(cli.httpx, "Client", Client)
    monkeypatch.setenv("XAA_API_TOKEN", "c" * 32)
    monkeypatch.setattr(sys, "argv", ["xaa", "chat", "--model", "local/model"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "Interrupt this run")
    monkeypatch.setattr(
        cli.time, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    cli.main()

    assert posts == ["/runs", "/runs/cancel-me/cancel"]
    assert "Cancellation requested for run cancel-me" in capsys.readouterr().out

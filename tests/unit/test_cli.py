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

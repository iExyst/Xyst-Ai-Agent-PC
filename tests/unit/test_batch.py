import json
import sys
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import httpx
import pytest
from xaa import batch as batch_module
from xaa import cli
from xaa.batch import execute_batch, load_batch


class BatchClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.requests: dict[str, dict[str, Any]] = {}
        self.run_states: dict[str, str] = {}
        self.exports: dict[str, bytes] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, path: str, *, json: dict[str, Any]) -> httpx.Response:
        self.calls.append(("POST", path, json))
        request_id = json["client_request_id"]
        record = self.requests.get(request_id)
        if record is None:
            record = {
                "id": str(uuid4()),
                "state": "queued",
                "payload": json,
            }
            self.requests[request_id] = record
            self.run_states[record["id"]] = "completed"
            self.exports[record["id"]] = json_bytes(
                {"format": "xaa-trajectory", "objective": json["objective"]}
            )
        return response(record)

    def get(self, path: str) -> httpx.Response:
        self.calls.append(("GET", path, None))
        parts = path.split("/")
        run_id = parts[2]
        if len(parts) == 4 and parts[3] == "export":
            return response(self.exports[run_id], content=self.exports[run_id])
        return response({"id": run_id, "state": self.run_states[run_id]})


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class FakeResponse:
    def __init__(self, value: Any, *, content: bytes | None = None) -> None:
        self.value = value
        self.content = content if content is not None else json_bytes(value)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.value


def response(value: Any, *, content: bytes | None = None) -> FakeResponse:
    return FakeResponse(value, content=content)


def write_batch(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "case-one",
                        "objective": "Read the README",
                        "acceptance_criteria": ["Cite its title"],
                    },
                    {
                        "id": "case-two",
                        "objective": "List the files",
                        "acceptance_criteria": ["Return file names"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_batch_runs_and_exports_each_case_then_resumes_without_resubmitting(
    workspace_tmp_path: Path,
) -> None:
    input_path = workspace_tmp_path / "batch.json"
    output_dir = workspace_tmp_path / "batch-output"
    write_batch(input_path)
    client = BatchClient()
    options = {
        "input_path": input_path,
        "output_dir": output_dir,
        "model": "fixture-model",
        "workspace": None,
        "mode": "observe",
        "max_iterations": 4,
        "max_seconds": 60,
        "max_output_tokens": 128,
        "timeout_seconds": 3,
        "poll_interval": 0.001,
    }

    first = execute_batch(client, **options, resume=False)
    assert first["summary"] == {"completed": 2}
    assert len(client.requests) == 2
    for case in first["cases"]:
        trajectory = output_dir / case["trajectory"]["path"]
        assert trajectory.read_bytes() == client.exports[case["run_id"]]
        assert case["trajectory"]["sha256"]
    post_count = len([call for call in client.calls if call[0] == "POST"])

    resumed = execute_batch(client, **options, resume=True)
    assert resumed["summary"] == {"completed": 2}
    assert len([call for call in client.calls if call[0] == "POST"]) == post_count


def test_batch_input_rejects_duplicate_case_ids(workspace_tmp_path: Path) -> None:
    path = workspace_tmp_path / "duplicate.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {"id": "same", "objective": "One", "acceptance_criteria": []},
                    {"id": "same", "objective": "Two", "acceptance_criteria": []},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unique, bounded cases"):
        load_batch(path)


def test_batch_resume_refuses_changed_input(workspace_tmp_path: Path) -> None:
    path = workspace_tmp_path / "batch.json"
    output_dir = workspace_tmp_path / "batch-output"
    write_batch(path)
    client = BatchClient()
    options = {
        "input_path": path,
        "output_dir": output_dir,
        "model": "fixture-model",
        "workspace": None,
        "mode": "observe",
        "max_iterations": 4,
        "max_seconds": 60,
        "max_output_tokens": 128,
        "timeout_seconds": 3,
        "poll_interval": 0.001,
    }
    execute_batch(client, **options, resume=False)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [{"id": "case-one", "objective": "Different", "acceptance_criteria": []}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="input or run settings differ"):
        execute_batch(client, **options, resume=True)


def test_cli_batch_command_uses_authenticated_api_and_writes_report(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = workspace_tmp_path / "batch.json"
    output_dir = workspace_tmp_path / "batch-output"
    write_batch(input_path)
    client = BatchClient()
    monkeypatch.setattr(cli.httpx, "Client", lambda **_kwargs: client)
    monkeypatch.setenv("XAA_API_TOKEN", "x" * 32)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xaa",
            "batch",
            str(input_path),
            "--output",
            str(output_dir),
            "--model",
            "fixture-model",
            "--poll-interval",
            "0.001",
        ],
    )

    cli.main()

    report = json.loads(capsys.readouterr().out)
    assert report["summary"] == {"completed": 2}
    assert len(client.requests) == 2


def test_batch_recovers_when_server_accepts_run_before_checkpoint_write(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = workspace_tmp_path / "batch.json"
    output_dir = workspace_tmp_path / "batch-output"
    write_batch(input_path)
    client = BatchClient()
    options = {
        "input_path": input_path,
        "output_dir": output_dir,
        "model": "fixture-model",
        "workspace": None,
        "mode": "observe",
        "max_iterations": 4,
        "max_seconds": 60,
        "max_output_tokens": 128,
        "timeout_seconds": 3,
        "poll_interval": 0.001,
    }
    write_manifest = batch_module._write_manifest
    write_count = 0

    def fail_after_submission(path: Path, manifest: batch_module.BatchManifest) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("simulated checkpoint interruption")
        write_manifest(path, manifest)

    monkeypatch.setattr(batch_module, "_write_manifest", fail_after_submission)
    with pytest.raises(OSError, match="checkpoint interruption"):
        execute_batch(client, **options, resume=False)
    assert len(client.requests) == 1
    accepted_id = next(iter(client.requests.values()))["id"]

    monkeypatch.setattr(batch_module, "_write_manifest", write_manifest)
    report = execute_batch(client, **options, resume=True)
    assert len(client.requests) == 2
    assert report["cases"][0]["run_id"] == accepted_id
    assert report["summary"] == {"completed": 2}

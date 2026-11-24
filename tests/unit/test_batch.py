import hashlib
import json
import json as json_module
import sys
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import httpx
import pytest
from xaa import batch as batch_module
from xaa import cli
from xaa.batch import (
    BatchCase,
    BatchCheck,
    BatchManifest,
    BatchSettings,
    TrajectoryInfo,
    execute_batch,
    export_training_jsonl,
    load_batch,
)


class BatchClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.requests: dict[str, dict[str, Any]] = {}
        self.run_states: dict[str, str] = {}
        self.exports: dict[str, bytes] = {}
        self.initial_state = "completed"
        self.export_content: bytes | None = None
        self.foreign_trajectory = False

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
            self.run_states[record["id"]] = self.initial_state
            if self.export_content is None:
                exported = {
                    "format": "xaa-trajectory",
                    "format_version": 1,
                    "run": {
                        "id": record["id"],
                        "objective": json["objective"],
                        "state": self.initial_state,
                    },
                    "events": [],
                }
            else:
                exported = json_module.loads(self.export_content)
                exported["run"]["id"] = record["id"]
            self.exports[record["id"]] = json_bytes(exported)
        return response(record)

    def get(self, path: str) -> httpx.Response:
        self.calls.append(("GET", path, None))
        parts = path.split("/")
        run_id = parts[2]
        if len(parts) == 4 and parts[3] == "export":
            content = self.exports[run_id]
            if self.foreign_trajectory:
                exported = json.loads(content)
                exported["run"]["id"] = "another-run"
                content = json_bytes(exported)
            return response(content, content=content)
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


def test_batch_returns_review_pending_candidate_without_timing_out(
    workspace_tmp_path: Path,
) -> None:
    input_path = workspace_tmp_path / "batch.json"
    output_dir = workspace_tmp_path / "batch-output"
    write_batch(input_path)
    client = BatchClient()
    client.initial_state = "verifying"
    options = {
        "input_path": input_path,
        "output_dir": output_dir,
        "model": "fixture-model",
        "workspace": None,
        "mode": "observe",
        "max_iterations": 4,
        "max_seconds": 60,
        "max_output_tokens": 128,
        "timeout_seconds": 1,
        "poll_interval": 0.001,
    }
    report = execute_batch(client, **options, resume=False)
    assert report["timed_out"] is False
    assert report["summary"] == {"verifying": 2}


def test_batch_rejects_trajectory_for_a_different_run(workspace_tmp_path: Path) -> None:
    input_path = workspace_tmp_path / "batch.json"
    output_dir = workspace_tmp_path / "batch-output"
    write_batch(input_path)
    client = BatchClient()
    client.foreign_trajectory = True

    with pytest.raises(ValueError, match="belongs to a different run"):
        execute_batch(
            client,
            input_path=input_path,
            output_dir=output_dir,
            model="fixture-model",
            workspace=None,
            mode="observe",
            max_iterations=4,
            max_seconds=60,
            max_output_tokens=128,
            timeout_seconds=1,
            poll_interval=0.001,
            resume=False,
        )


def test_batch_scores_structured_checks_against_exported_trajectory(
    workspace_tmp_path: Path,
) -> None:
    input_path = workspace_tmp_path / "scored-batch.json"
    input_path.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "checked",
                        "objective": "Read the fixture nonce",
                        "acceptance_criteria": ["Read and return the nonce"],
                        "checks": [
                            {"kind": "final_equals", "expected": "NONCE-42"},
                            {"kind": "final_contains", "expected": "NONCE-42"},
                            {"kind": "tool_called", "expected": "workspace_read_file"},
                            {"kind": "tool_completed", "expected": "workspace.read_file"},
                            {"kind": "state_equals", "expected": "completed"},
                            {"kind": "artifact_count_equals", "expected": "1"},
                            {"kind": "artifact_sha256_exists", "expected": "a" * 64},
                            {"kind": "artifact_media_type_exists", "expected": "text/plain"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_dir = workspace_tmp_path / "scored-output"
    client = BatchClient()
    client.export_content = json_bytes(
        {
            "format": "xaa-trajectory",
            "format_version": 1,
            "run": {
                "id": "fixture-run",
                "objective": "Read the fixture nonce",
                "state": "completed",
            },
            "events": [
                {
                    "event_type": "model.message",
                    "payload": {
                        "message": {
                            "role": "assistant",
                            "content": "The fixture says NONCE-42.",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {"name": "workspace_read_file"},
                                }
                            ],
                        }
                    },
                },
                {
                    "event_type": "tool.intended",
                    "payload": {
                        "call_id": "call-1",
                        "tool": "workspace.read_file",
                        "internal": False,
                    },
                },
                {
                    "event_type": "tool.completed",
                    "payload": {"call_id": "call-1", "internal": False},
                },
            ],
            "artifacts": [
                {
                    "id": "artifact-1",
                    "media_type": "text/plain",
                    "size_bytes": 12,
                    "sha256": "a" * 64,
                    "provenance": "workspace.write_file",
                    "created_at": "2026-11-24T00:00:00Z",
                }
            ],
        }
    )

    report = execute_batch(
        client,
        input_path=input_path,
        output_dir=output_dir,
        model="fixture-model",
        workspace=None,
        mode="observe",
        max_iterations=2,
        max_seconds=60,
        max_output_tokens=128,
        timeout_seconds=1,
        poll_interval=0.001,
        resume=False,
    )

    case = report["cases"][0]
    assert case["evaluation"] == "failed"
    assert [result["passed"] for result in case["check_results"]] == [
        False,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
    ]
    assert report["evaluation_summary"] == {
        "evaluated_cases": 1,
        "passed_cases": 0,
        "failed_cases": 1,
        "passed_checks": 7,
        "failed_checks": 1,
    }
    submitted_payload = client.requests[next(iter(client.requests))]["payload"]
    assert submitted_payload["objective"] == "Read the fixture nonce"
    assert "checks" not in submitted_payload


@pytest.mark.parametrize("format_version", [None, 2])
def test_batch_scoring_rejects_missing_or_unsupported_trajectory_version(
    format_version: int | None,
) -> None:
    trajectory: dict[str, Any] = {
        "format": "xaa-trajectory",
        "run": {"id": "fixture-run", "objective": "Read the fixture nonce", "state": "completed"},
        "events": [],
    }
    if format_version is not None:
        trajectory["format_version"] = format_version
    case = BatchCase(
        id="checked",
        objective="Read the fixture nonce",
        acceptance_criteria=[],
        checks=[BatchCheck(kind="state_equals", expected="completed")],
    )

    with pytest.raises(ValueError):
        batch_module.score_trajectory(case, json_bytes(trajectory))


def test_batch_final_text_check_does_not_reuse_text_before_tool_call() -> None:
    case = BatchCase(
        id="checked",
        objective="Read a file",
        acceptance_criteria=[],
        checks=[BatchCheck(kind="final_contains", expected="stale answer")],
    )
    trajectory = {
        "format": "xaa-trajectory",
        "format_version": 1,
        "run": {"id": "fixture-run", "objective": "Read a file", "state": "completed"},
        "events": [
            {
                "event_type": "model.message",
                "payload": {"message": {"content": "stale answer"}},
            },
            {
                "event_type": "model.message",
                "payload": {
                    "message": {
                        "content": None,
                        "tool_calls": [{"function": {"name": "workspace_read_file"}}],
                    }
                },
            },
        ],
    }

    [result] = batch_module.score_trajectory(case, json_bytes(trajectory))

    assert result.actual is False
    assert result.passed is False


@pytest.mark.parametrize(
    ("artifact_count", "expected", "passed"), [(0, "0", True), (1, "1", True), (1, "2", False)]
)
def test_batch_can_check_exported_artifact_count(
    artifact_count: int, expected: str, passed: bool
) -> None:
    case = BatchCase(
        id="checked",
        objective="Produce a report",
        acceptance_criteria=[],
        checks=[BatchCheck(kind="artifact_count_equals", expected=expected)],
    )
    artifacts = [
        {
            "id": f"artifact-{index}",
            "media_type": "text/plain",
            "size_bytes": 7,
            "sha256": "a" * 64,
            "provenance": "workspace.write_file",
            "created_at": "2026-11-24T00:00:00Z",
        }
        for index in range(artifact_count)
    ]
    trajectory = {
        "format": "xaa-trajectory",
        "format_version": 1,
        "run": {"id": "fixture-run", "objective": "Produce a report", "state": "completed"},
        "events": [],
        "artifacts": artifacts,
    }

    [result] = batch_module.score_trajectory(case, json_bytes(trajectory))

    assert result.actual == str(artifact_count)
    assert result.passed is passed


@pytest.mark.parametrize("expected", ["-1", "1.5", " 1", "10001"])
def test_batch_artifact_count_check_rejects_invalid_counts(expected: str) -> None:
    with pytest.raises(ValueError, match="Artifact count must be a decimal integer"):
        BatchCheck(kind="artifact_count_equals", expected=expected)


@pytest.mark.parametrize(
    ("artifact_hash", "expected", "passed"),
    [("a" * 64, "a" * 64, True), ("a" * 64, "b" * 64, False)],
)
def test_batch_can_check_exported_artifact_sha256(
    artifact_hash: str, expected: str, passed: bool
) -> None:
    case = BatchCase(
        id="checked",
        objective="Produce a report",
        acceptance_criteria=[],
        checks=[BatchCheck(kind="artifact_sha256_exists", expected=expected)],
    )
    trajectory = {
        "format": "xaa-trajectory",
        "format_version": 1,
        "run": {"id": "fixture-run", "objective": "Produce a report", "state": "completed"},
        "events": [],
        "artifacts": [
            {
                "id": "artifact-1",
                "media_type": "text/plain",
                "size_bytes": 7,
                "sha256": artifact_hash,
                "provenance": "workspace.write_file",
                "created_at": "2026-11-24T00:00:00Z",
            }
        ],
    }

    [result] = batch_module.score_trajectory(case, json_bytes(trajectory))

    assert result.actual is passed
    assert result.passed is passed


@pytest.mark.parametrize("expected", ["A" * 64, "0" * 63, "not-a-hash"])
def test_batch_artifact_sha256_check_rejects_invalid_digest(expected: str) -> None:
    with pytest.raises(ValueError, match="Artifact SHA-256 must be 64 lowercase hexadecimal"):
        BatchCheck(kind="artifact_sha256_exists", expected=expected)


@pytest.mark.parametrize(
    ("media_type", "expected", "passed"),
    [("image/png", "image/png", True), ("text/plain", "image/png", False)],
)
def test_batch_can_check_exported_artifact_media_type(
    media_type: str, expected: str, passed: bool
) -> None:
    case = BatchCase(
        id="checked",
        objective="Produce an image",
        acceptance_criteria=[],
        checks=[BatchCheck(kind="artifact_media_type_exists", expected=expected)],
    )
    trajectory = {
        "format": "xaa-trajectory",
        "format_version": 1,
        "run": {"id": "fixture-run", "objective": "Produce an image", "state": "completed"},
        "events": [],
        "artifacts": [
            {
                "id": "artifact-1",
                "media_type": media_type,
                "size_bytes": 7,
                "sha256": "a" * 64,
                "provenance": "workspace.write_file",
                "created_at": "2026-11-24T00:00:00Z",
            }
        ],
    }

    [result] = batch_module.score_trajectory(case, json_bytes(trajectory))

    assert result.actual is passed
    assert result.passed is passed


def _training_fixture(batch_dir: Path) -> None:
    batch_dir.mkdir()
    (batch_dir / "trajectories").mkdir()
    run_id = "run-reviewed"
    trajectory = json_bytes(
        {
            "format": "xaa-trajectory",
            "format_version": 1,
            "run": {"id": run_id, "objective": "Summarize the safe fixture", "state": "completed"},
            "events": [
                {
                    "event_type": "model.message",
                    "payload": {"message": {"content": "The fixture is safe."}},
                }
            ],
        }
    )
    path = batch_dir / "trajectories" / "reviewed.json"
    path.write_bytes(trajectory)
    case = BatchCase(
        id="reviewed",
        objective="Private manifest objective must not be exported",
        acceptance_criteria=[],
        checks=[BatchCheck(kind="final_equals", expected="The fixture is safe.")],
        run_id=run_id,
        state="completed",
        trajectory=TrajectoryInfo(
            path="trajectories/reviewed.json",
            size_bytes=len(trajectory),
            sha256=hashlib.sha256(trajectory).hexdigest(),
        ),
        evaluation="passed",
    )
    manifest = BatchManifest(
        batch_id="batch-fixture",
        input_digest="0" * 64,
        settings=BatchSettings(model="fixture", mode="observe", max_iterations=1, max_seconds=1),
        created_at="2026-11-07T00:00:00Z",
        cases=[case],
    )
    (batch_dir / "batch.json").write_text(manifest.model_dump_json(), encoding="utf-8")


def test_training_export_includes_only_accepted_scored_text_and_refuses_overwrite(
    workspace_tmp_path: Path,
) -> None:
    batch_dir = workspace_tmp_path / "batch"
    _training_fixture(batch_dir)
    output = workspace_tmp_path / "training.jsonl"
    report = export_training_jsonl(batch_dir, output)
    assert report["included_case_ids"] == ["reviewed"]
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "messages": [
            {"role": "user", "content": "Summarize the safe fixture"},
            {"role": "assistant", "content": "The fixture is safe."},
        ]
    }
    assert "Private manifest objective" not in output.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="overwrite"):
        export_training_jsonl(batch_dir, output)


def test_training_export_does_not_publish_stale_text_after_empty_final_message(
    workspace_tmp_path: Path,
) -> None:
    batch_dir = workspace_tmp_path / "batch"
    _training_fixture(batch_dir)
    trajectory_path = batch_dir / "trajectories" / "reviewed.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["events"].append(
        {"event_type": "model.message", "payload": {"message": {"content": None}}}
    )
    content = json_bytes(trajectory)
    trajectory_path.write_bytes(content)

    manifest_path = batch_dir / "batch.json"
    manifest = BatchManifest.model_validate_json(manifest_path.read_bytes())
    case = manifest.cases[0]
    case.checks = [BatchCheck(kind="state_equals", expected="completed")]
    case.trajectory = TrajectoryInfo(
        path="trajectories/reviewed.json",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    output = workspace_tmp_path / "training.jsonl"
    with pytest.raises(ValueError, match="No user-accepted"):
        export_training_jsonl(batch_dir, output)
    assert not output.exists()


def test_training_export_rejects_trajectory_integrity_mismatch(workspace_tmp_path: Path) -> None:
    batch_dir = workspace_tmp_path / "batch"
    _training_fixture(batch_dir)
    trajectory = batch_dir / "trajectories" / "reviewed.json"
    trajectory.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        export_training_jsonl(batch_dir, workspace_tmp_path / "out.jsonl")


@pytest.mark.parametrize("format_version", [None, 2])
def test_training_export_rejects_missing_or_unsupported_trajectory_version(
    workspace_tmp_path: Path, format_version: int | None
) -> None:
    batch_dir = workspace_tmp_path / "batch"
    _training_fixture(batch_dir)
    trajectory_path = batch_dir / "trajectories" / "reviewed.json"
    trajectory = json.loads(trajectory_path.read_bytes())
    if format_version is None:
        del trajectory["format_version"]
    else:
        trajectory["format_version"] = format_version
    content = json_bytes(trajectory)
    trajectory_path.write_bytes(content)

    manifest_path = batch_dir / "batch.json"
    manifest = BatchManifest.model_validate_json(manifest_path.read_bytes())
    manifest.cases[0].trajectory = TrajectoryInfo(
        path="trajectories/reviewed.json",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError):
        export_training_jsonl(batch_dir, workspace_tmp_path / "out.jsonl")


def test_training_export_cli_is_local_and_does_not_need_api_token(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_dir = workspace_tmp_path / "batch"
    _training_fixture(batch_dir)
    monkeypatch.delenv("XAA_API_TOKEN", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xaa",
            "export-training",
            str(batch_dir),
            "--output",
            str(workspace_tmp_path / "out.jsonl"),
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["included_examples"] == 1


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

"""Bounded, resumable execution and trajectory capture for evaluation batches."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MAX_BATCH_BYTES = 1_048_576
MAX_BATCH_CASES = 32
MAX_TRAJECTORY_BYTES = 10 * 1024 * 1024
MAX_TRAINING_EXPORT_BYTES = 10 * 1024 * 1024
ACTIVE_RUN_STATES = {"queued", "planning", "running"}
CASE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class BatchCheck(BaseModel):
    """Deterministic check applied to the public trajectory after the run stops."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["final_equals", "final_contains", "tool_called", "tool_completed", "state_equals"]
    expected: str = Field(min_length=1, max_length=10_000)


class BatchCheckResult(BaseModel):
    kind: Literal["final_equals", "final_contains", "tool_called", "tool_completed", "state_equals"]
    expected: str
    actual: str | bool
    passed: bool


class BatchEvaluationSummary(BaseModel):
    evaluated_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    passed_checks: int = 0
    failed_checks: int = 0


class _TrajectoryToolFunction(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    name: str


class _TrajectoryToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    function: _TrajectoryToolFunction


def _empty_trajectory_tool_calls() -> list[_TrajectoryToolCall]:
    return []


class _TrajectoryMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    content: str | None = None
    tool_calls: list[_TrajectoryToolCall] = Field(default_factory=_empty_trajectory_tool_calls)


class _TrajectoryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    message: _TrajectoryMessage | None = None
    call_id: str | None = None
    tool: str | None = None
    internal: bool | None = None


class _TrajectoryEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    event_type: str
    payload: _TrajectoryPayload


class _TrajectoryRun(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str
    objective: str
    state: str


class _TrajectoryDocument(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    format: Literal["xaa-trajectory"]
    run: _TrajectoryRun
    events: list[_TrajectoryEvent]


def _empty_batch_checks() -> list[BatchCheck]:
    return []


class BatchInputCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=CASE_ID_PATTERN)
    objective: str = Field(min_length=1, max_length=10_000)
    acceptance_criteria: list[str] = Field(max_length=50)
    checks: list[BatchCheck] = Field(default_factory=_empty_batch_checks, max_length=20)

    @field_validator("objective")
    @classmethod
    def validate_objective(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Objective cannot be blank.")
        return value

    @field_validator("acceptance_criteria")
    @classmethod
    def validate_criteria(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 2_000 for item in value):
            raise ValueError("Acceptance criteria must be non-empty strings up to 2000 characters.")
        return value


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    cases: list[BatchInputCase] = Field(min_length=1, max_length=MAX_BATCH_CASES)

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> BatchInput:
        identifiers = [case.id for case in self.cases]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Batch case IDs must be unique.")
        return self


class BatchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model: str = Field(min_length=1, max_length=256)
    workspace: str | None = Field(default=None, max_length=2048)
    mode: Literal["observe", "workspace"]
    max_iterations: int = Field(ge=1, le=100)
    max_seconds: int = Field(ge=1, le=86400)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class TrajectoryInfo(BaseModel):
    path: str
    size_bytes: int
    sha256: str


def _empty_batch_check_results() -> list[BatchCheckResult]:
    return []


class BatchCase(BatchInputCase):
    run_id: str | None = None
    state: str = "not_submitted"
    trajectory: TrajectoryInfo | None = None
    check_results: list[BatchCheckResult] = Field(default_factory=_empty_batch_check_results)
    evaluation: Literal["not_evaluated", "passed", "failed"] = "not_evaluated"


class BatchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["xaa-batch"] = "xaa-batch"
    version: Literal[1] = 1
    batch_id: str
    input_digest: str
    settings: BatchSettings
    created_at: str
    cases: list[BatchCase]
    timed_out: bool = False
    summary: dict[str, int] = Field(default_factory=dict)
    evaluation_summary: BatchEvaluationSummary = Field(default_factory=BatchEvaluationSummary)


def score_trajectory(case: BatchCase, content: bytes) -> list[BatchCheckResult]:
    if not case.checks:
        return []
    trajectory = _TrajectoryDocument.model_validate_json(content)
    final_content = ""
    tool_names: set[str] = set()
    intended_tools: dict[str, str] = {}
    completed_call_ids: set[str] = set()
    for event in trajectory.events:
        if event.event_type == "model.message" and event.payload.message is not None:
            message = event.payload.message
            if message.content:
                final_content = message.content
            tool_names.update(call.function.name for call in message.tool_calls)
        elif event.payload.internal is False and event.payload.call_id:
            if event.event_type == "tool.intended" and event.payload.tool:
                intended_tools[event.payload.call_id] = event.payload.tool
            elif event.event_type == "tool.completed":
                completed_call_ids.add(event.payload.call_id)
    completed_tools = {
        intended_tools[call_id] for call_id in completed_call_ids if call_id in intended_tools
    }
    results: list[BatchCheckResult] = []
    for check in case.checks:
        if check.kind == "final_equals":
            actual: str | bool = final_content == check.expected
        elif check.kind == "final_contains":
            actual = check.expected in final_content
        elif check.kind == "tool_called":
            actual = check.expected in tool_names
        elif check.kind == "tool_completed":
            actual = check.expected in completed_tools
        else:
            actual = trajectory.run.state
        results.append(
            BatchCheckResult(
                kind=check.kind,
                expected=check.expected,
                actual=actual,
                passed=actual is True if isinstance(actual, bool) else actual == check.expected,
            )
        )
    return results


def load_batch(path: Path) -> tuple[BatchInput, str]:
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_BATCH_BYTES:
        raise ValueError("Batch input must be between 1 byte and 1 MiB.")
    try:
        document = BatchInput.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(
            "Batch input is invalid; provide a version 1 file with unique, bounded cases."
        ) from exc
    return document, hashlib.sha256(raw).hexdigest()


def _write_manifest(path: Path, manifest: BatchManifest) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    encoded = manifest.model_dump_json(indent=2).encode("utf-8") + b"\n"
    try:
        with temporary.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def export_training_jsonl(batch_dir: Path, output_path: Path) -> dict[str, Any]:
    """Export only reviewed, scored, text-only examples from a batch checkpoint."""
    root = batch_dir.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Batch path must be a directory.")
    manifest_path = root / "batch.json"
    try:
        manifest = BatchManifest.model_validate_json(manifest_path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ValueError("Cannot export training data: batch checkpoint is invalid.") from exc

    rows: list[str] = []
    included_case_ids: list[str] = []
    skipped: dict[str, int] = {}
    for case in manifest.cases:
        if case.state != "completed":
            reason = "awaiting_or_rejected_review"
        elif case.evaluation != "passed" or not case.checks:
            reason = "evaluation_not_passed"
        elif case.run_id is None or case.trajectory is None:
            reason = "missing_trajectory"
        else:
            trajectory_path = (root / case.trajectory.path).resolve(strict=True)
            if not trajectory_path.is_relative_to(root):
                raise ValueError(f"Trajectory path escapes batch output for case {case.id}.")
            content = trajectory_path.read_bytes()
            if (
                not content
                or len(content) > MAX_TRAJECTORY_BYTES
                or len(content) != case.trajectory.size_bytes
                or hashlib.sha256(content).hexdigest() != case.trajectory.sha256
            ):
                raise ValueError(f"Trajectory integrity check failed for case {case.id}.")
            trajectory = _TrajectoryDocument.model_validate_json(content)
            if trajectory.run.id != case.run_id:
                raise ValueError(f"Trajectory run identity does not match case {case.id}.")
            if trajectory.run.state != "completed":
                raise ValueError(
                    f"Trajectory for case {case.id} does not show accepted completion."
                )
            checks = score_trajectory(case, content)
            if not checks or not all(check.passed for check in checks):
                reason = "evaluation_not_passed"
            elif any(
                call
                for event in trajectory.events
                if event.event_type == "model.message" and event.payload.message is not None
                for call in event.payload.message.tool_calls
            ):
                reason = "tool_trace_not_supported"
            else:
                final_content = ""
                for event in trajectory.events:
                    if (
                        event.event_type == "model.message"
                        and event.payload.message is not None
                        and event.payload.message.content
                    ):
                        final_content = event.payload.message.content
                objective = trajectory.run.objective.strip()
                if not objective or not final_content.strip():
                    reason = "empty_text"
                else:
                    rows.append(
                        json.dumps(
                            {
                                "messages": [
                                    {"role": "user", "content": objective},
                                    {"role": "assistant", "content": final_content},
                                ]
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                    included_case_ids.append(case.id)
                    continue
        skipped[reason] = skipped.get(reason, 0) + 1

    if not rows:
        raise ValueError(
            "No user-accepted, check-passing, text-only examples are eligible for training export."
        )
    payload = ("\n".join(rows) + "\n").encode("utf-8")
    if len(payload) > MAX_TRAINING_EXPORT_BYTES:
        raise ValueError("Training export exceeds the 10 MiB limit.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, output_path)
        except FileExistsError as exc:
            raise ValueError("Refusing to overwrite an existing training export.") from exc
        temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "source_batch_id": manifest.batch_id,
        "output": str(output_path),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "included_case_ids": included_case_ids,
        "included_examples": len(included_case_ids),
        "skipped_cases": skipped,
    }


def execute_batch(
    client: httpx.Client,
    *,
    input_path: Path,
    output_dir: Path,
    model: str,
    workspace: str | None,
    mode: Literal["observe", "workspace"],
    max_iterations: int,
    max_seconds: int,
    max_output_tokens: int | None,
    timeout_seconds: int,
    poll_interval: float,
    resume: bool,
) -> dict[str, Any]:
    if not 1 <= timeout_seconds <= 86400 or not 0 < poll_interval <= 60:
        raise ValueError("Batch wait and polling limits are out of range.")
    batch_input, input_digest = load_batch(input_path)
    settings = BatchSettings(
        model=model,
        workspace=workspace,
        mode=mode,
        max_iterations=max_iterations,
        max_seconds=max_seconds,
        max_output_tokens=max_output_tokens,
    )
    manifest_path = output_dir / "batch.json"
    if output_dir.exists():
        if not resume:
            raise ValueError(
                "Batch output directory already exists; pass --resume to continue its checkpoint."
            )
        try:
            manifest = BatchManifest.model_validate_json(manifest_path.read_bytes())
        except (OSError, ValidationError) as exc:
            raise ValueError(
                "Cannot resume: output directory has no valid batch checkpoint."
            ) from exc
        if manifest.input_digest != input_digest or manifest.settings != settings:
            raise ValueError(
                "Cannot resume: batch input or run settings differ from the saved checkpoint."
            )
        saved_cases = [
            case.model_dump(include={"id", "objective", "acceptance_criteria", "checks"})
            for case in manifest.cases
        ]
        if saved_cases != [case.model_dump() for case in batch_input.cases]:
            raise ValueError("Cannot resume: checkpoint cases do not match the input file.")
    else:
        if resume:
            raise ValueError("Cannot resume: batch output directory does not exist.")
        output_dir.mkdir(parents=True)
        (output_dir / "trajectories").mkdir()
        manifest = BatchManifest(
            batch_id=uuid4().hex,
            input_digest=input_digest,
            settings=settings,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            cases=[BatchCase(**case.model_dump()) for case in batch_input.cases],
        )
        _write_manifest(manifest_path, manifest)

    batch_id = manifest.batch_id
    for case in manifest.cases:
        if case.run_id is not None:
            continue
        payload = {
            "objective": case.objective,
            "acceptance_criteria": case.acceptance_criteria,
            "workspace": workspace,
            "mode": mode,
            "model": model,
            "max_iterations": max_iterations,
            "max_seconds": max_seconds,
            "max_output_tokens": max_output_tokens,
            "client_request_id": f"batch:{batch_id}:{case.id}",
        }
        response = client.post("/runs", json=payload)
        response.raise_for_status()
        run = cast(dict[str, Any], response.json())
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError(f"Engine returned an invalid run ID for batch case {case.id}.")
        case.run_id = run_id
        case.state = str(run.get("state", "queued"))
        _write_manifest(manifest_path, manifest)

    deadline = time.monotonic() + timeout_seconds
    while True:
        active = False
        for case in manifest.cases:
            if case.run_id is None:
                raise RuntimeError(f"Batch case {case.id} has no persisted run ID.")
            response = client.get(f"/runs/{case.run_id}")
            response.raise_for_status()
            run = cast(dict[str, Any], response.json())
            state = run.get("state")
            if not isinstance(state, str):
                raise TypeError(f"Engine returned an invalid state for batch case {case.id}.")
            case.state = state
            active = active or state in ACTIVE_RUN_STATES
        manifest.timed_out = active and time.monotonic() >= deadline
        _write_manifest(manifest_path, manifest)
        if not active or manifest.timed_out:
            break
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    trajectory_directory = output_dir / "trajectories"
    trajectory_directory.mkdir(exist_ok=True)
    for case in manifest.cases:
        if case.run_id is None:
            raise RuntimeError(f"Batch case {case.id} has no persisted run ID.")
        response = client.get(f"/runs/{case.run_id}/export")
        response.raise_for_status()
        content = response.content
        if not content or len(content) > MAX_TRAJECTORY_BYTES:
            raise ValueError(f"Trajectory for batch case {case.id} is empty or exceeds 10 MiB.")
        try:
            exported = _TrajectoryDocument.model_validate_json(content)
        except ValidationError as exc:
            raise ValueError(f"Trajectory for batch case {case.id} is invalid.") from exc
        if exported.run.id != case.run_id:
            raise ValueError(f"Trajectory for batch case {case.id} belongs to a different run.")
        case.state = exported.run.state
        trajectory = trajectory_directory / f"{case.id}.json"
        temporary = trajectory.with_name(f".{trajectory.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            temporary.replace(trajectory)
        finally:
            temporary.unlink(missing_ok=True)
        case.trajectory = TrajectoryInfo(
            path=str(Path("trajectories") / trajectory.name),
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        case.check_results = score_trajectory(case, content)
        if case.check_results:
            case.evaluation = (
                "passed" if all(result.passed for result in case.check_results) else "failed"
            )
    states = {case.state for case in manifest.cases}
    manifest.summary = {
        state: sum(case.state == state for case in manifest.cases) for state in states
    }
    evaluated_cases = [case for case in manifest.cases if case.check_results]
    manifest.evaluation_summary = BatchEvaluationSummary(
        evaluated_cases=len(evaluated_cases),
        passed_cases=sum(case.evaluation == "passed" for case in evaluated_cases),
        failed_cases=sum(case.evaluation == "failed" for case in evaluated_cases),
        passed_checks=sum(
            result.passed for case in evaluated_cases for result in case.check_results
        ),
        failed_checks=sum(
            not result.passed for case in evaluated_cases for result in case.check_results
        ),
    )
    _write_manifest(manifest_path, manifest)
    return manifest.model_dump(mode="json")

"""Bounded, resumable execution and trajectory capture for evaluation batches."""

from __future__ import annotations

import hashlib
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
ACTIVE_RUN_STATES = {"queued", "planning", "running", "verifying"}
CASE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class BatchInputCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=CASE_ID_PATTERN)
    objective: str = Field(min_length=1, max_length=10_000)
    acceptance_criteria: list[str] = Field(max_length=50)

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


class BatchCase(BatchInputCase):
    run_id: str | None = None
    state: str = "not_submitted"
    trajectory: TrajectoryInfo | None = None


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
            case.model_dump(include={"id", "objective", "acceptance_criteria"})
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
    states = {case.state for case in manifest.cases}
    manifest.summary = {
        state: sum(case.state == state for case in manifest.cases) for state in states
    }
    _write_manifest(manifest_path, manifest)
    return manifest.model_dump(mode="json")

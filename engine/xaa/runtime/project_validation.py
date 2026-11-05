"""Explicit final-project checks and reviewable evidence bound to current project bytes."""

import json
import time
from typing import Any
from uuid import uuid4

from pydantic import ConfigDict, Field

from xaa.api.contracts import ProjectValidationEvidence, ProjectValidationSummary
from xaa.runtime.command_contracts import CommandArguments
from xaa.runtime.command_controller import CommandController
from xaa.runtime.task_commands import verify_command_output
from xaa.runtime.tool_worker import execute_filesystem
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore


class ProjectValidationRequest(CommandArguments):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9-]{16,64}$")
    expected_revision: int = Field(ge=0)


def validation_required(store: RunStore, run_id: str) -> bool:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    if run.get("command_grant") is None:
        return False
    with store.connection() as connection:
        return (
            connection.execute(
                "SELECT 1 FROM tool_calls WHERE run_id=? AND task_id IS NULL AND state='completed' AND tool_name IN ('workspace.write_file','workspace.create_file','workspace.replace_text','workspace.apply_patch') AND json_extract(result,'$.sha256') IS NOT NULL LIMIT 1",
                (run_id,),
            ).fetchone()
            is not None
        )


async def summary(store: RunStore, run_id: str, owner: str) -> ProjectValidationSummary:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    required = validation_required(store, run_id)
    if run.get("command_grant") is None:
        return ProjectValidationSummary(
            available=False, required=required, current_snapshot_sha256=None, evidence=[]
        )
    journal = CommandJournal(store, purpose="project_validation")
    with store.connection() as connection:
        store.require_lease(connection, run_id, owner)
        journal.require_state(connection, run_id)
    identity = "project-observe-" + uuid4().hex
    observed = await execute_filesystem(
        store, run_id, owner, identity, "project_hash", {}, idempotency_key=identity, internal=True
    )
    evidence: list[ProjectValidationEvidence] = []
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT tc.provider_call_id,tc.arguments,tc.result FROM tool_calls tc JOIN run_events e ON e.run_id=tc.run_id AND e.event_type='execution.reserved' AND json_extract(e.payload,'$.call_id')=tc.provider_call_id WHERE tc.run_id=? AND tc.task_id IS NULL AND tc.tool_name='workspace.run_command' AND tc.tool_version='1' AND tc.state='completed' AND json_extract(e.payload,'$.purpose')='project_validation' ORDER BY tc.created_at DESC LIMIT 16",
            (run_id,),
        ).fetchall()
        unresolved = [
            row[0]
            for row in connection.execute(
                "SELECT tc.provider_call_id FROM tool_calls tc JOIN run_events e ON e.run_id=tc.run_id AND e.event_type='execution.reserved' AND json_extract(e.payload,'$.call_id')=tc.provider_call_id WHERE tc.run_id=? AND tc.task_id IS NULL AND tc.tool_name='workspace.run_command' AND tc.state='intended' AND json_extract(e.payload,'$.purpose')='project_validation'",
                (run_id,),
            )
        ]
    for row in reversed(rows):
        result = json.loads(row["result"])
        verify_command_output(store, run_id, result)
        evidence.append(
            ProjectValidationEvidence(
                call_id=row["provider_call_id"],
                argv=json.loads(row["arguments"])["argv"],
                matches_current_project=observed["sha256"] == result["snapshot_sha256"],
                **{
                    key: result[key]
                    for key in (
                        "snapshot_sha256",
                        "succeeded",
                        "exit_code",
                        "termination_reason",
                        "stdout_excerpt",
                        "stderr_excerpt",
                        "stdout_artifact_id",
                        "stderr_artifact_id",
                    )
                },
            )
        )
    return ProjectValidationSummary(
        available=True,
        required=required,
        current_snapshot_sha256=observed["sha256"],
        evidence=evidence,
        unresolved_call_ids=unresolved,
    )


async def validate_project(
    store: RunStore, run_id: str, request: ProjectValidationRequest
) -> dict[str, Any]:
    owner = store.acquire_lease(run_id, "local-user-project-validation", seconds=180)
    if owner is None:
        raise ValueError("An active worker still owns this run.")
    try:
        run = store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["revision"] != request.expected_revision:
            raise ValueError("Run changed; refresh final review.")
        journal = CommandJournal(store, purpose="project_validation")
        with store.connection() as connection:
            journal.require_state(connection, run_id)
        grant = journal.grant(run_id, owner=owner)
        if request.max_seconds > grant.max_seconds:
            raise PermissionError("Validation duration exceeds the saved grant.")
        deadline = time.monotonic() + 90
        transport = await WSLTransport.discover(grant.distribution, deadline=deadline)
        if transport.profile_sha256 != grant.profile_sha256:
            raise PermissionError("The execution profile changed after authorization.")
        identity = "project-validation-" + request.request_id
        return await CommandController(store, transport, purpose="project_validation").execute(
            run_id,
            owner,
            identity,
            {"argv": request.argv, "max_seconds": request.max_seconds},
            idempotency_key=identity,
            deadline=deadline,
        )
    finally:
        store.release_lease(run_id, owner)

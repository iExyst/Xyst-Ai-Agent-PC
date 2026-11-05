"""Explicit user decisions on final candidates with independent file rechecks."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.project_validation import summary as project_validation_summary
from xaa.runtime.project_validation import validation_required
from xaa.runtime.verification import FileContentCheck, verify_files_owned
from xaa.storage.runs import RunStore


class RunReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["accept", "reject"]
    expected_revision: int = Field(ge=0)
    rationale: str = Field(min_length=1, max_length=4000)
    objective_confirmed: bool = False
    confirmed_criteria: list[int] = Field(default_factory=lambda: list[int](), max_length=50)
    validation_call_ids: list[str] = Field(default_factory=list, max_length=16)


async def review_run(store: RunStore, run_id: str, decision: RunReviewDecision) -> dict[str, Any]:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    if run["state"] != "verifying" or run["revision"] != decision.expected_revision:
        raise ValueError("Run changed or is not awaiting review.")
    if not decision.rationale.strip():
        raise ValueError("Explain the final review decision.")
    token = store.acquire_lease(run_id, "local-user-final-review", seconds=180)
    if token is None:
        raise ValueError("An active worker still owns this run.")
    try:
        checked = 0
        validation_record: dict[str, Any] = {}
        if decision.decision == "accept":
            store.resolve_interrupted_reads(run_id, token)
            if not decision.objective_confirmed or sorted(decision.confirmed_criteria) != list(
                range(len(run["acceptance_criteria"]))
            ):
                raise ValueError(
                    "Confirm the objective and every acceptance criterion exactly once."
                )
            history = store.conversation_messages(run_id)
            if (
                not history
                or history[-1].get("role") != "assistant"
                or history[-1].get("tool_calls")
                or not history[-1].get("content", "").strip()
            ):
                raise ValueError("No final candidate is available for review.")
            if store.unresolved_tool_calls(run_id):
                raise ValueError("Uncertain tool actions require reconciliation.")
            checks = [FileContentCheck.model_validate(check) for check in run["file_checks"]]
            if await verify_files_owned(store, run_id, token, checks):
                raise ValueError(
                    "Independent file checks failed; inspect the current files before accepting."
                )
            checked = len(checks)
            if validation_required(store, run_id):
                selected = decision.validation_call_ids
                if not selected or len(set(selected)) != len(selected):
                    raise ValueError(
                        "Select successful validation evidence for the current project."
                    )
                current = await project_validation_summary(store, run_id, token)
                evidence = {item.call_id: item for item in current.evidence}
                if any(
                    call_id not in evidence
                    or not evidence[call_id].succeeded
                    or not evidence[call_id].matches_current_project
                    for call_id in selected
                ):
                    raise ValueError(
                        "Selected validation evidence is failed, stale, or unavailable."
                    )
                validation_record = {
                    "validation_call_ids": selected,
                    "validation_snapshot_sha256": current.current_snapshot_sha256,
                }
        return store.commit_run_review(
            run_id,
            token,
            revision=decision.expected_revision,
            accepted=decision.decision == "accept",
            review={**decision.model_dump(), **validation_record, "file_checks_passed": checked},
        )
    finally:
        store.release_lease(run_id, token)

"""Schema-validated model proposals for durable specialist plans."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.executor import ReconciliationRequired
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


class ProposedTask(TaskDefinition):
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    dependencies: tuple[str, ...] = Field()


class SavePlanArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tasks: list[ProposedTask] = Field(min_length=1, max_length=64)


def validate_model_plan(
    store: RunStore, run_id: str, arguments: dict[str, Any]
) -> SavePlanArguments:
    proposal = SavePlanArguments.model_validate(arguments)
    tasks = TaskStore(store)
    # Check all authority and graph constraints before journaling an invalid proposal.
    tasks.validate_plan(run_id, list(proposal.tasks))
    run = store.get_run(run_id)
    assert run is not None
    if (
        sum(task.max_iterations for task in proposal.tasks) + 1 + store.inference_attempts(run_id)
        > run["limits"]["max_iterations"]
    ):
        raise ValueError(
            "Plan must fit the remaining shared budget, including one parent response."
        )
    if tasks.records(run_id):
        raise ValueError("A saved plan cannot be silently replaced.")
    return proposal


def reject_model_plan(
    store: RunStore, run_id: str, token: str, call_id: str, arguments: dict[str, Any], *, code: str
) -> dict[str, Any]:
    if not store.record_tool_intent(
        run_id,
        token,
        call_id=call_id,
        tool="plan.save",
        version="1",
        arguments={"rejected_proposal": arguments},
        idempotency_key=call_id,
    ):
        raise ReconciliationRequired("Rejected proposal already journaled; inspect saved state.")
    run = store.get_run(run_id)
    assert run is not None
    message = (
        "No plan was saved. Correct schema, dependencies, permissions and budgets before proposing again. "
        "Each attempt consumes the shared run budget."
    )
    if code == "plan_save_must_be_unique_final_call":
        message = (
            "No tool from this assistant response was executed and no plan was saved. Retry with "
            "exactly one plan_save call, as the final tool call in the response."
        )
    result: dict[str, Any] = {
        "status": "rejected",
        "code": code,
        "message": message,
        "remaining_child_iterations": max(
            0, run["limits"]["max_iterations"] - store.inference_attempts(run_id) - 1
        ),
    }
    store.record_tool_result(run_id, token, call_id, result)
    store.append_worker_event(run_id, token, "plan.rejected", {"call_id": call_id, "code": code})
    return result


def save_model_plan(
    store: RunStore, run_id: str, token: str, call_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    proposal = validate_model_plan(store, run_id, arguments)
    tasks = TaskStore(store)
    if not store.record_tool_intent(
        run_id,
        token,
        call_id=call_id,
        tool="plan.save",
        version="1",
        arguments=proposal.model_dump(),
        idempotency_key=call_id,
    ):
        raise ReconciliationRequired("Plan action already exists; inspect stored state.")
    tasks.save_plan(run_id, token, list(proposal.tasks), parent_headroom=1)
    ready, blocked = tasks.readiness(run_id)
    result: dict[str, Any] = {
        "task_ids": [task.id for task in proposal.tasks],
        "ready": ready,
        "blocked": blocked,
        "status": "saved",
        "execution": "Tasks are saved but specialist execution has not been dispatched.",
    }
    store.record_tool_result(run_id, token, call_id, result)
    return result

"""Validated task dependencies and permission/budget inheritance."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=10000)
    acceptance_criteria: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    role: Literal["planner", "researcher", "developer", "reviewer", "tester", "executor"] = (
        "executor"
    )
    permissions: tuple[str, ...] = ("workspace.read",)
    max_iterations: int = Field(default=4, ge=1, le=100)
    max_seconds: int = Field(default=120, ge=1, le=86400)
    max_total_tokens: int | None = Field(default=None, ge=1, le=100_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)

    @field_validator("acceptance_criteria", "dependencies", "permissions", mode="before")
    @classmethod
    def freeze_sequences(cls, value: Any) -> Any:
        return tuple(cast(list[Any], value)) if isinstance(value, list) else value


TaskStatus = Literal[
    "queued", "running", "verifying", "completed", "failed", "blocked", "cancelled"
]


class TaskGraph:
    def __init__(
        self,
        tasks: list[TaskDefinition],
        *,
        parent_permissions: set[str],
        iteration_budget: int,
        seconds_budget: int,
        token_budget: int | None = None,
        output_limit: int | None = None,
    ) -> None:
        if not tasks or len(tasks) > 64:
            raise ValueError("A plan must contain between 1 and 64 tasks.")
        self._tasks = MappingProxyType({task.id: task for task in tasks})
        if len(self.tasks) != len(tasks):
            raise ValueError("Task IDs must be unique.")
        if (
            sum(task.max_iterations for task in tasks) > iteration_budget
            or sum(task.max_seconds for task in tasks) > seconds_budget
        ):
            raise ValueError("Task reservations exceed the shared parent budget.")
        if (
            token_budget is not None
            and sum(task.max_total_tokens or 0 for task in tasks) > token_budget
        ):
            raise ValueError("Declared task token allowances exceed the remaining shared budget.")
        for task in tasks:
            if (
                output_limit is not None
                and task.max_output_tokens is not None
                and task.max_output_tokens > output_limit
            ):
                raise PermissionError("Child output cap cannot exceed the parent output cap.")
            if not set(task.permissions).issubset(parent_permissions):
                raise PermissionError("Child task cannot broaden parent permissions.")
            if len(set(task.dependencies)) != len(task.dependencies):
                raise ValueError("Duplicate dependency.")
            if any(identity not in self.tasks for identity in task.dependencies):
                raise ValueError("Dependency refers to an unknown task.")
        visited: set[str] = set()
        active: set[str] = set()

        def visit(identity: str) -> None:
            if identity in active:
                raise ValueError("Task graph contains a dependency cycle.")
            if identity in visited:
                return
            active.add(identity)
            for dependency in self.tasks[identity].dependencies:
                visit(dependency)
            active.remove(identity)
            visited.add(identity)

        for identity in self.tasks:
            visit(identity)

    @property
    def tasks(self) -> Mapping[str, TaskDefinition]:
        return self._tasks

    def readiness(self, statuses: dict[str, TaskStatus]) -> tuple[list[str], list[str]]:
        if set(statuses) != set(self.tasks):
            raise ValueError("Every task must have exactly one status.")
        blocked = {
            identity
            for identity, state in statuses.items()
            if state in {"failed", "blocked", "cancelled"}
        }
        changed = True
        while changed:
            previous = len(blocked)
            blocked.update(
                task.id
                for task in self.tasks.values()
                if statuses[task.id] == "queued"
                and any(dependency in blocked for dependency in task.dependencies)
            )
            changed = len(blocked) != previous
        ready = [
            task.id
            for task in self.tasks.values()
            if statuses[task.id] == "queued"
            and task.id not in blocked
            and all(statuses[dependency] == "completed" for dependency in task.dependencies)
        ]
        newly_blocked = [
            identity
            for identity in self.tasks
            if identity in blocked and statuses[identity] == "queued"
        ]
        return ready, newly_blocked

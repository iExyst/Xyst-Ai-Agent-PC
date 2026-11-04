"""Public API records, excluding internal worker authority."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from xaa.runtime.command_contracts import CommandGrant
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskStatus
from xaa.runtime.verification import FileContentCheck
from xaa.storage.skills import SkillTrialRecord


class TaskBudgets(BaseModel):
    max_iterations: int = Field(ge=1, le=100)
    max_seconds: int = Field(ge=1, le=86400)
    max_total_tokens: int | None = Field(default=None, ge=1, le=100_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class RunLimits(TaskBudgets):
    pass


class RunRecord(BaseModel):
    id: str
    objective: str
    acceptance_criteria: list[str]
    workspace: str | None
    mode: Literal["observe", "workspace", "authorized_automation"]
    model: str | None
    state: RunState
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=0)
    limits: RunLimits
    file_checks: list[FileContentCheck]
    procedure_trial: SkillTrialRecord | None = None
    command_grant: CommandGrant | None = None


class RunEvent(BaseModel):
    sequence: int = Field(ge=1)
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class RunSummary(BaseModel):
    id: str
    objective: str
    state: RunState
    model: str | None
    workspace: str | None
    created_at: datetime
    updated_at: datetime


class RunHistory(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None


class UsageReport(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    unmeasured_calls: int = Field(ge=0)
    outstanding_reserved_tokens: int = Field(ge=0)
    outstanding_reservations: int = Field(ge=0)


class ArtifactRecord(BaseModel):
    id: str
    run_id: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    provenance: str
    created_at: datetime


class MemoryRecord(BaseModel):
    id: str
    kind: str
    scope: str
    content: str
    provenance: str
    confidence: float | None
    created_at: datetime
    updated_at: datetime
    schema_version: int


class PlanTaskRecord(BaseModel):
    id: str
    local_id: str
    objective: str
    acceptance_criteria: list[str]
    dependencies: list[str]
    assigned_role: str
    model_route: str | None
    workspace: str | None
    permission_scope: list[str]
    budgets: TaskBudgets
    status: TaskStatus
    blocked_reason: (
        Literal["token_budget_exhausted_or_unmeasured", "dependency_failed", "execution_blocked"]
        | None
    ) = None


class PlanRecord(BaseModel):
    tasks: list[PlanTaskRecord]
    ready: list[str]
    blocked: list[str]


class TaskEvidence(BaseModel):
    call_id: str
    path: str
    sha256: str
    excerpt: str
    excerpt_truncated: bool


class TaskCandidate(BaseModel):
    task_id: str
    status: TaskStatus
    candidate: str | None
    acceptance_criteria: list[str]
    evidence: list[TaskEvidence]

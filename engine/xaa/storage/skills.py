"""Immutable user-authored procedure candidates; text never grants execution authority."""

import builtins
import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from xaa.storage.runs import RunStore, utc_now


class SkillTool(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
    version: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,31}$")


class SkillDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=16384)
    required_tools: list[SkillTool] = Field(max_length=32)
    required_permissions: list[Literal["workspace.read", "workspace.write"]] = Field(max_length=2)

    @model_validator(mode="after")
    def valid_manifest(self) -> "SkillDraft":
        if (
            not self.name.strip()
            or not self.content.strip()
            or len(self.content.encode("utf-8")) > 16384
        ):
            raise ValueError("Procedure needs a name and 1-16384 UTF-8 bytes of text.")
        if len({tool.name for tool in self.required_tools}) != len(self.required_tools):
            raise ValueError("Tool dependencies must have unique names.")
        if len(set(self.required_permissions)) != len(self.required_permissions):
            raise ValueError("Permissions must be unique.")
        return self


class SkillTrial(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    skill_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1, le=128)


class SkillTrialRecord(SkillTrial):
    name: str
    snapshot_sha256: str


TRIAL_TOOLS = {
    "workspace.read_file": {"workspace.read"},
    "workspace.list_directory": {"workspace.read"},
    "workspace.search": {"workspace.read"},
    "workspace.write_file": {"workspace.write"},
    "workspace.create_file": {"workspace.write"},
    "workspace.replace_text": {"workspace.read", "workspace.write"},
    "workspace.apply_patch": {"workspace.read", "workspace.write"},
}


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    manifest = {key: snapshot[key] for key in ("content", "required_tools", "required_permissions")}
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def resolve_trial(
    connection: sqlite3.Connection, selection: SkillTrial, *, mode: str, workspace: str | None
) -> dict[str, Any]:
    if mode not in {"observe", "workspace", "authorized_automation"}:
        raise ValueError("Invalid procedure trial operating mode.")
    row = connection.execute(
        "SELECT skill_versions.*,skills.name,skills.lifecycle_state FROM skill_versions JOIN skills ON skills.id=skill_versions.skill_id WHERE skill_id=? AND version=?",
        (selection.skill_id, selection.version),
    ).fetchone()
    if row is None:
        raise ValueError("Selected procedure version no longer exists.")
    if row["lifecycle_state"] not in {"candidate", "evaluated"}:
        raise ValueError("Only candidate or evaluated procedures can be selected for a trial.")
    tools = json.loads(row["required_tools"])
    permissions = set(json.loads(row["required_permissions"]))
    granted: set[str] = (
        {"workspace.read"} if mode == "observe" else {"workspace.read", "workspace.write"}
    )
    if not workspace:
        granted = set()
    if not permissions <= granted:
        raise ValueError("Procedure requirements exceed this run's workspace permission scope.")
    for tool in tools:
        if tool["version"] != "1" or tool["name"] not in TRIAL_TOOLS:
            raise ValueError("A required procedure tool/version is unavailable for trials.")
        if not TRIAL_TOOLS[tool["name"]] <= permissions:
            raise ValueError("Procedure manifest omits a required tool permission.")
    snapshot: dict[str, Any] = {
        "skill_id": selection.skill_id,
        "version": selection.version,
        "name": row["name"],
        "content": row["content"],
        "required_tools": tools,
        "required_permissions": sorted(permissions),
        "purpose": "evaluation_trial",
    }
    snapshot["snapshot_sha256"] = snapshot_digest(snapshot)
    return snapshot


class SkillRevision(SkillDraft):
    expected_version: int = Field(ge=1, le=127)


class SkillSummary(BaseModel):
    id: str
    name: str
    lifecycle_state: Literal["candidate", "evaluated"]
    active_version: None
    latest_version: int
    created_at: datetime
    updated_at: datetime


class TrialReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=128)
    case_description: str = Field(min_length=1, max_length=500)


class SkillEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_evaluation_count: int = Field(ge=0, le=31)
    trials: list[TrialReview] = Field(min_length=2, max_length=10)
    criteria: list[str] = Field(min_length=1, max_length=20)
    confirmed_criteria: list[int] = Field(max_length=20)
    rationale: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def explicit_review(self) -> "SkillEvaluation":
        if len({trial.run_id for trial in self.trials}) != len(self.trials):
            raise ValueError("Evaluation needs distinct trial runs.")
        descriptions = [trial.case_description.strip().casefold() for trial in self.trials]
        if not all(descriptions) or len(set(descriptions)) != len(descriptions):
            raise ValueError("Describe each distinct representative case.")
        if (
            any(not text.strip() or len(text) > 500 for text in self.criteria)
            or not self.rationale.strip()
        ):
            raise ValueError("Evaluation criteria and rationale need meaningful bounded text.")
        if sorted(self.confirmed_criteria) != list(range(len(self.criteria))):
            raise ValueError("Confirm every evaluation criterion exactly once.")
        return self


class EvaluationTrialRecord(TrialReview):
    objective: str
    run_revision: int
    final_review_sequence: int


class EvaluationRecord(BaseModel):
    id: str
    actor: Literal["local-user"]
    evaluated_at: datetime
    snapshot_sha256: str
    trials: list[EvaluationTrialRecord]
    criteria: list[str]
    confirmed_criteria: list[int]
    rationale: str


class SkillEvidence(BaseModel):
    state: Literal["candidate", "evaluated"]
    provenance: Literal["user"]
    evaluations: list[EvaluationRecord]


class SkillVersion(BaseModel):
    skill_id: str
    version: int
    content: str
    required_tools: list[SkillTool]
    required_permissions: list[str]
    evaluation_evidence: SkillEvidence
    created_at: datetime


class SkillTrialRun(BaseModel):
    id: str
    objective: str
    created_at: datetime


class SkillConflict(ValueError):
    """A concurrent edit or duplicate name must be resolved explicitly."""


class SkillStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    @staticmethod
    def _insert_version(
        connection: sqlite3.Connection, identity: str, version: int, draft: SkillDraft
    ) -> None:
        connection.execute(
            "INSERT INTO skill_versions VALUES (?,?,?,?,?,?,?)",
            (
                identity,
                version,
                draft.content,
                json.dumps([tool.model_dump() for tool in draft.required_tools]),
                json.dumps(draft.required_permissions),
                json.dumps({"state": "candidate", "provenance": "user", "evaluations": []}),
                utc_now(),
            ),
        )

    def create(self, draft: SkillDraft) -> dict[str, Any]:
        identity, now = str(uuid4()), utc_now()
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT COUNT(*) FROM skills").fetchone()[0] >= 100:
                raise SkillConflict("The current procedure library limit is 100.")
            try:
                connection.execute(
                    "INSERT INTO skills VALUES (?,?,'candidate',NULL,?,?)",
                    (identity, draft.name.strip(), now, now),
                )
            except sqlite3.IntegrityError:
                raise SkillConflict("A procedure with that name already exists.") from None
            self._insert_version(connection, identity, 1, draft)
        return self.get(identity)

    def revise(self, identity: str, draft: SkillRevision) -> dict[str, Any]:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lifecycle_state FROM skills WHERE id=?", (identity,)
            ).fetchone()
            if row is None:
                raise KeyError(identity)
            latest = connection.execute(
                "SELECT MAX(version) FROM skill_versions WHERE skill_id=?", (identity,)
            ).fetchone()[0]
            if latest != draft.expected_version or row[0] not in {"candidate", "evaluated"}:
                raise SkillConflict("Procedure changed; reload its latest candidate version.")
            try:
                connection.execute(
                    "UPDATE skills SET name=?,updated_at=?,lifecycle_state='candidate' WHERE id=?",
                    (draft.name.strip(), utc_now(), identity),
                )
            except sqlite3.IntegrityError:
                raise SkillConflict("A procedure with that name already exists.") from None
            self._insert_version(connection, identity, latest + 1, draft)
        return self.get(identity)

    def list(self) -> list[dict[str, Any]]:
        with self.runs.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT skills.*,MAX(skill_versions.version) AS latest_version FROM skills JOIN skill_versions ON skills.id=skill_versions.skill_id GROUP BY skills.id ORDER BY skills.updated_at DESC,skills.id LIMIT 100"
                )
            ]

    def get(self, identity: str) -> dict[str, Any]:
        row = next((row for row in self.list() if row["id"] == identity), None)
        if row is None:
            raise KeyError(identity)
        return row

    def version(self, identity: str, version: int) -> dict[str, Any]:
        if not 1 <= version <= 128:
            raise ValueError("Invalid procedure version.")
        with self.runs.connection() as connection:
            row = connection.execute(
                "SELECT * FROM skill_versions WHERE skill_id=? AND version=?", (identity, version)
            ).fetchone()
        if row is None:
            raise KeyError(identity)
        value = dict(row)
        for key in ("required_tools", "required_permissions", "evaluation_evidence"):
            value[key] = json.loads(value[key])
        return value

    def eligible_trials(self, identity: str, version: int) -> builtins.list[dict[str, Any]]:
        manifest = self.version(identity, version)
        manifest["required_permissions"] = sorted(manifest["required_permissions"])
        digest = snapshot_digest(manifest)
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT runs.id,runs.objective,runs.created_at FROM runs WHERE state='completed' "
                "AND EXISTS (SELECT 1 FROM run_events WHERE run_id=runs.id AND event_type='procedure.selected' "
                "AND json_extract(payload,'$.skill_id')=? AND json_extract(payload,'$.version')=? "
                "AND json_extract(payload,'$.snapshot_sha256')=? AND json_extract(payload,'$.purpose')='evaluation_trial') "
                "AND NOT EXISTS (SELECT 1 FROM tool_calls WHERE run_id=runs.id AND state='intended') "
                "AND EXISTS (SELECT 1 FROM run_events WHERE run_id=runs.id AND event_type='procedure.context_loaded' "
                "AND json_extract(payload,'$.snapshot_sha256')=?) "
                "AND EXISTS (SELECT 1 FROM run_events WHERE run_id=runs.id AND event_type='run.reviewed' "
                "AND json_extract(payload,'$.decision')='accept' AND json_extract(payload,'$.actor')='local-user') "
                "ORDER BY runs.created_at DESC,runs.id DESC LIMIT 50",
                (identity, version, digest, digest),
            ).fetchall()
        return [dict(row) for row in rows]

    def evaluate(self, identity: str, version: int, review: SkillEvaluation) -> dict[str, Any]:
        if not 1 <= version <= 128:
            raise ValueError("Invalid procedure version.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM skill_versions WHERE skill_id=? AND version=?", (identity, version)
            ).fetchone()
            if row is None:
                raise KeyError(identity)
            evidence = json.loads(row["evaluation_evidence"])
            prior = evidence.get("evaluations", [])
            if len(prior) != review.expected_evaluation_count:
                raise SkillConflict(
                    "Evaluation history changed; reload before recording another review."
                )
            manifest = {
                key: json.loads(row[key]) if key != "content" else row[key]
                for key in ("content", "required_tools", "required_permissions")
            }
            manifest["required_permissions"] = sorted(manifest["required_permissions"])
            digest = snapshot_digest(manifest)
            trials: list[dict[str, Any]] = []
            for trial in review.trials:
                run = connection.execute(
                    "SELECT * FROM runs WHERE id=?", (trial.run_id,)
                ).fetchone()
                events = connection.execute(
                    "SELECT sequence,event_type,payload FROM run_events WHERE run_id=? ORDER BY sequence",
                    (trial.run_id,),
                ).fetchall()
                selected = next(
                    (
                        json.loads(event["payload"])
                        for event in events
                        if event["event_type"] == "procedure.selected"
                    ),
                    None,
                )
                accepted = [
                    event
                    for event in events
                    if event["event_type"] == "run.reviewed"
                    and json.loads(event["payload"]).get("decision") == "accept"
                    and json.loads(event["payload"]).get("actor") == "local-user"
                ]
                loaded = any(
                    event["event_type"] == "procedure.context_loaded"
                    and (
                        json.loads(event["payload"]).get("skill_id"),
                        json.loads(event["payload"]).get("version"),
                        json.loads(event["payload"]).get("snapshot_sha256"),
                    )
                    == (identity, version, digest)
                    for event in events
                )
                if (
                    run is None
                    or run["state"] != "completed"
                    or not accepted
                    or not loaded
                    or selected is None
                ):
                    raise SkillConflict(
                        "Each trial must be completed through explicit final review after loading this procedure."
                    )
                if selected.get("purpose") != "evaluation_trial":
                    raise SkillConflict("A run was not selected as a procedure evaluation trial.")
                if (
                    selected.get("skill_id"),
                    selected.get("version"),
                    selected.get("snapshot_sha256"),
                ) != (identity, version, digest) or snapshot_digest(selected) != digest:
                    raise SkillConflict("A trial used a different procedure version or manifest.")
                if connection.execute(
                    "SELECT 1 FROM tool_calls WHERE run_id=? AND state='intended' LIMIT 1",
                    (trial.run_id,),
                ).fetchone():
                    raise SkillConflict("A trial still has unresolved tool actions.")
                trials.append(
                    {
                        **trial.model_dump(),
                        "objective": run["objective"],
                        "run_revision": run["revision"],
                        "final_review_sequence": accepted[-1]["sequence"],
                    }
                )
            entry = {
                "id": str(uuid4()),
                "actor": "local-user",
                "evaluated_at": utc_now(),
                "snapshot_sha256": digest,
                "trials": trials,
                "criteria": review.criteria,
                "confirmed_criteria": review.confirmed_criteria,
                "rationale": review.rationale,
            }
            evidence = {**evidence, "state": "evaluated", "evaluations": [*prior, entry]}
            connection.execute(
                "UPDATE skill_versions SET evaluation_evidence=? WHERE skill_id=? AND version=?",
                (json.dumps(evidence), identity, version),
            )
            latest = connection.execute(
                "SELECT MAX(version) FROM skill_versions WHERE skill_id=?", (identity,)
            ).fetchone()[0]
            if latest == version:
                connection.execute(
                    "UPDATE skills SET lifecycle_state='evaluated',updated_at=? WHERE id=?",
                    (utc_now(), identity),
                )
        return self.version(identity, version)

    def delete(self, identity: str) -> None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute(
                    "DELETE FROM skills WHERE id=? AND lifecycle_state IN ('candidate','evaluated')",
                    (identity,),
                ).rowcount
                != 1
            ):
                raise KeyError(identity)

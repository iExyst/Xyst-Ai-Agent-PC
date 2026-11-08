"""Immutable user-authored procedure candidates; text never grants execution authority."""

import base64
import builtins
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from xaa.storage.runs import RunStore, utc_now

MAX_PROCEDURE_TEXT_BYTES = 80 * 1024

_HERMES_TOOL_ALIASES = {
    "read_file": "workspace_read_file",
    "workspace.read_file": "workspace_read_file",
    "list_directory": "workspace_list_directory",
    "workspace.list_directory": "workspace_list_directory",
    "write_file": "workspace_write_file",
    "workspace.write_file": "workspace_write_file",
    "create_file": "workspace_create_file",
    "workspace.create_file": "workspace_create_file",
    "replace_text": "workspace_replace_text",
    "workspace.replace_text": "workspace_replace_text",
    "apply_patch": "workspace_apply_patch",
    "workspace.apply_patch": "workspace_apply_patch",
    "search_files": "workspace_search",
    "web_search": "web_search",
    "web_fetch": "web_fetch",
    "browser_run": "browser_run",
    "terminal": "workspace_run_command",
}


def available_toolsets(tool_names: set[str]) -> set[str]:
    """Return capability groups backed by tools exposed in this run."""
    file_tools = {
        "workspace_read_file",
        "workspace_list_directory",
        "workspace_create_file",
        "workspace_write_file",
        "workspace_replace_text",
        "workspace_apply_patch",
        "workspace_search",
    }
    result: set[str] = set()
    if tool_names & file_tools:
        result.update({"file", "files", "workspace"})
    if "workspace_run_command" in tool_names:
        result.update({"terminal", "workspace"})
    if tool_names & {"web_search", "web_fetch"}:
        result.add("web")
    if "browser_run" in tool_names:
        result.add("browser")
    if tool_names & {"workspace_search", "web_search"}:
        result.add("search")
    if any(name.startswith("mcp_") for name in tool_names):
        result.add("mcp")
    if tool_names & {"procedure_view", "procedure_read_resource", "procedure_catalog"}:
        result.add("skills")
    return result


def host_platform_name() -> str:
    """Return the normalized operating-system name used by skill metadata."""
    return (
        "windows"
        if sys.platform == "win32"
        else "macos"
        if sys.platform == "darwin"
        else "linux"
        if sys.platform.startswith("linux")
        else "other"
    )


def procedure_matches_tool_conditions(
    requirements: dict[str, Any],
    *,
    available_tools: set[str],
    toolsets: set[str],
    current_platform: str | None = None,
) -> bool:
    """Apply Hermes requires/fallback visibility rules without granting authority."""

    def values(key: str) -> list[str]:
        raw_value: Any = requirements.get(key, [])
        if not isinstance(raw_value, list):
            return []
        raw = cast(list[Any], raw_value)
        return [item.strip().lower() for item in raw[:64] if isinstance(item, str) and item.strip()]

    available = set(available_tools)
    available.update(
        declared for declared, actual in _HERMES_TOOL_ALIASES.items() if actual in available_tools
    )
    required_tools = values("hermes_required_tools")
    fallback_tools = values("hermes_fallback_tools")
    required_toolsets = values("hermes_toolsets")
    fallback_toolsets = values("hermes_fallback_toolsets")
    declared_platforms = values("platforms")
    host_platform = current_platform or host_platform_name()
    known_platforms = {"windows", "macos", "linux"}
    supported_platforms = set(declared_platforms) & known_platforms
    if declared_platforms and (not supported_platforms or host_platform not in supported_platforms):
        return False
    known_tools = available | set(_HERMES_TOOL_ALIASES)
    # Unknown required capabilities cannot be satisfied. Unknown fallback names
    # are absent, so they do not hide an otherwise usable fallback.
    if any(name not in known_tools or name not in available for name in required_tools):
        return False
    if any(name not in toolsets for name in required_toolsets):
        return False
    if any(name in available for name in fallback_tools):
        return False
    return not any(name in toolsets for name in fallback_toolsets)


class SkillTool(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
    version: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,31}$")


class SkillAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    path: str = Field(min_length=1, max_length=512)
    content_base64: str = Field(max_length=90_000)
    media_type: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def bounded_text_asset(self) -> "SkillAsset":
        from pathlib import PurePosixPath

        normalized = PurePosixPath(self.path)
        if (
            normalized.is_absolute()
            or normalized.as_posix() != self.path
            or any(part in {"", ".", ".."} for part in normalized.parts)
            or "\\" in self.path
        ):
            raise ValueError("Procedure resource paths must be normalized relative POSIX paths.")
        try:
            raw = base64.b64decode(self.content_base64, validate=True)
        except ValueError as exc:
            raise ValueError("Procedure resource is not valid base64.") from exc
        if len(raw) > 64 * 1024 or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("Procedure resource exceeds 64 KiB or failed its digest check.")
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Procedure resources must contain UTF-8 text.") from exc
        return self


class SkillDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=MAX_PROCEDURE_TEXT_BYTES)
    description: str = Field(default="", max_length=1024)
    category: str = Field(default="", max_length=64, pattern=r"^(?:[a-z0-9]+(?:-[a-z0-9]+)*)?$")
    required_tools: list[SkillTool] = Field(max_length=32)
    required_permissions: list[Literal["workspace.read", "workspace.write"]] = Field(max_length=2)
    assets: list[SkillAsset] = Field(default_factory=list[SkillAsset], max_length=32)

    @model_validator(mode="after")
    def valid_manifest(self) -> "SkillDraft":
        if (
            not self.name.strip()
            or not self.content.strip()
            or len(self.content.encode("utf-8")) > MAX_PROCEDURE_TEXT_BYTES
        ):
            raise ValueError(
                f"Procedure needs a name and 1-{MAX_PROCEDURE_TEXT_BYTES} UTF-8 bytes of text."
            )
        if len({tool.name for tool in self.required_tools}) != len(self.required_tools):
            raise ValueError("Tool dependencies must have unique names.")
        if self.description and not self.description.strip():
            raise ValueError("Procedure descriptions must contain visible text.")
        if len(set(self.required_permissions)) != len(self.required_permissions):
            raise ValueError("Permissions must be unique.")
        if len({asset.path for asset in self.assets}) != len(self.assets):
            raise ValueError("Procedure resource paths must be unique.")
        if (
            sum(len(base64.b64decode(asset.content_base64, validate=True)) for asset in self.assets)
            > 1024 * 1024
        ):
            raise ValueError("Procedure resources exceed the 1 MiB combined content limit.")
        return self


class SkillContentRevision(BaseModel):
    """A model may only replace content on the explicitly selected procedure version."""

    model_config = ConfigDict(extra="forbid", strict=True)
    skill_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1, le=127)
    content: str = Field(min_length=1, max_length=MAX_PROCEDURE_TEXT_BYTES)

    @model_validator(mode="after")
    def bounded_content(self) -> "SkillContentRevision":
        if not self.content.strip() or len(self.content.encode("utf-8")) > MAX_PROCEDURE_TEXT_BYTES:
            raise ValueError("Procedure content must contain visible text within the byte limit.")
        return self


class SkillTrial(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    skill_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1, le=128)
    purpose: Literal["evaluation_trial", "active_use"] = "evaluation_trial"


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
    for key in ("description", "category"):
        if snapshot.get(key):
            manifest[key] = snapshot[key]
    if snapshot.get("assets"):
        manifest["assets"] = [
            {key: asset[key] for key in ("path", "media_type", "sha256")}
            for asset in snapshot["assets"]
        ]
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def resolve_trial(
    connection: sqlite3.Connection, selection: SkillTrial, *, mode: str, workspace: str | None
) -> dict[str, Any]:
    if mode not in {"observe", "workspace", "authorized_automation"}:
        raise ValueError("Invalid procedure trial operating mode.")
    row = connection.execute(
        "SELECT skill_versions.*,skills.name,skills.lifecycle_state,skills.active_version FROM skill_versions JOIN skills ON skills.id=skill_versions.skill_id WHERE skill_id=? AND version=?",
        (selection.skill_id, selection.version),
    ).fetchone()
    if row is None:
        raise ValueError("Selected procedure version no longer exists.")
    if row["lifecycle_state"] == "deprecated":
        raise ValueError("Deprecated procedures cannot be selected for new runs.")
    if selection.purpose == "active_use" and (
        row["lifecycle_state"] != "active" or row["active_version"] != selection.version
    ):
        raise ValueError("Normal procedure use requires the currently active version.")
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
        "purpose": selection.purpose,
    }
    evidence = json.loads(row["evaluation_evidence"])
    if evidence.get("description"):
        snapshot["description"] = evidence["description"]
    if evidence.get("category"):
        snapshot["category"] = evidence["category"]
    assets: list[sqlite3.Row] = connection.execute(
        "SELECT relative_path,media_type,sha256,content FROM skill_version_assets "
        "WHERE skill_id=? AND version=? ORDER BY relative_path",
        (selection.skill_id, selection.version),
    ).fetchall()
    if assets:
        snapshot["assets"] = [
            {
                "path": asset["relative_path"],
                "media_type": asset["media_type"],
                "sha256": asset["sha256"],
                "content_base64": base64.b64encode(asset["content"]).decode("ascii"),
            }
            for asset in assets
        ]
    snapshot["snapshot_sha256"] = snapshot_digest(snapshot)
    return snapshot


class SkillRevision(SkillDraft):
    expected_version: int = Field(ge=1, le=127)


class SkillSummary(BaseModel):
    id: str
    name: str
    description: str = Field(default="", max_length=1024)
    category: str = Field(default="", max_length=64)
    lifecycle_state: Literal["candidate", "evaluated", "active", "deprecated"]
    active_version: int | None
    lifecycle_revision: int
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
    provenance: Literal[
        "user", "model_draft", "model_revision", "agent_plugin", "agent_plugins_v1", "hermes_skill"
    ]
    source: dict[str, str] | None = None
    evaluations: list[EvaluationRecord]


class SkillVersion(BaseModel):
    skill_id: str
    version: int
    content: str
    description: str = ""
    category: str = ""
    required_tools: list[SkillTool]
    required_permissions: list[str]
    evaluation_evidence: SkillEvidence
    created_at: datetime
    assets: list[SkillAsset] = Field(default_factory=list[SkillAsset])


class SkillTrialRun(BaseModel):
    id: str
    objective: str
    created_at: datetime


class SkillLifecycleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["activate", "rollback", "deprecate"]
    target_version: int | None = Field(default=None, ge=1, le=128)
    expected_active_version: int | None = Field(ge=1, le=128)
    expected_latest_version: int = Field(ge=1, le=128)
    expected_lifecycle_revision: int = Field(ge=0, le=255)
    rationale: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def explicit_action(self) -> "SkillLifecycleDecision":
        if not self.rationale.strip():
            raise ValueError("Explain the procedure lifecycle decision.")
        if (self.action == "deprecate") != (self.target_version is None):
            raise ValueError("Activation/rollback need a target; deprecation does not.")
        return self


class SkillLifecycleEvent(BaseModel):
    sequence: int
    action: Literal["activate", "rollback", "deprecate"]
    actor: Literal["local-user"]
    from_version: int | None
    to_version: int | None
    rationale: str
    snapshot_sha256: str | None
    created_at: datetime


class SkillConflict(ValueError):
    """A concurrent edit or duplicate name must be resolved explicitly."""


class SkillStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    @staticmethod
    def _insert_version(
        connection: sqlite3.Connection,
        identity: str,
        version: int,
        draft: SkillDraft,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        version_evidence = dict(
            evidence or {"state": "candidate", "provenance": "user", "evaluations": []}
        )
        version_evidence["description"] = draft.description
        version_evidence["category"] = draft.category
        connection.execute(
            "INSERT INTO skill_versions VALUES (?,?,?,?,?,?,?)",
            (
                identity,
                version,
                draft.content,
                json.dumps([tool.model_dump() for tool in draft.required_tools]),
                json.dumps(draft.required_permissions),
                json.dumps(version_evidence),
                utc_now(),
            ),
        )
        for asset in draft.assets:
            raw = base64.b64decode(asset.content_base64, validate=True)
            connection.execute(
                "INSERT INTO skill_version_assets VALUES (?,?,?,?,?,?,?)",
                (identity, version, asset.path, asset.media_type, len(raw), asset.sha256, raw),
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

    def create_model_draft_from_run(
        self, run_id: str, owner: str, call_id: str, draft: SkillDraft
    ) -> dict[str, Any]:
        """Atomically save an explicitly authorized model candidate and its tool result."""
        if self.runs.task_scope is not None:
            raise PermissionError("Specialists cannot create user-library procedures.")
        if not call_id or len(call_id) > 128:
            raise ValueError("Procedure draft call identity is invalid.")
        identity = str(uuid4())
        now = utc_now()
        encoded_arguments = json.dumps(draft.model_dump(mode="json"), sort_keys=True)
        evidence: dict[str, Any] = {
            "state": "candidate",
            "provenance": "model_draft",
            "source": {"run_id": run_id, "call_id": call_id},
            "evaluations": [],
        }
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, owner)
            grant = connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='procedure.drafting_granted'",
                (run_id,),
            ).fetchone()
            if grant is None:
                raise PermissionError("This run has no user authorization to draft procedures.")
            if connection.execute(
                "SELECT 1 FROM tool_calls WHERE run_id=? AND provider_call_id=?", (run_id, call_id)
            ).fetchone():
                raise SkillConflict("The model procedure-draft call was already used.")
            drafts_this_run = int(
                connection.execute(
                    "SELECT COUNT(*) FROM run_events WHERE run_id=? AND event_type='procedure.draft_created'",
                    (run_id,),
                ).fetchone()[0]
            )
            if drafts_this_run >= 3:
                raise SkillConflict("A run may create at most three procedure candidates.")
            if connection.execute("SELECT COUNT(*) FROM skills").fetchone()[0] >= 100:
                raise SkillConflict("The current procedure library limit is 100.")
            try:
                connection.execute(
                    "INSERT INTO skills VALUES (?,?,'candidate',NULL,?,?)",
                    (identity, draft.name.strip(), now, now),
                )
            except sqlite3.IntegrityError:
                raise SkillConflict("A procedure with that name already exists.") from None
            self._insert_version(connection, identity, 1, draft, evidence)
            summary = {
                "id": identity,
                "name": draft.name.strip(),
                "description": draft.description,
                "category": draft.category,
                "lifecycle_state": "candidate",
                "active_version": None,
                "lifecycle_revision": 0,
                "latest_version": 1,
                "created_at": now,
                "updated_at": now,
            }
            result = {
                "status": "candidate_created",
                "procedure": summary,
                "trust": "untrusted_model_draft",
                "requires_user_review": True,
                "notice": "The candidate was not evaluated, activated, selected, or executed.",
            }
            connection.execute(
                "INSERT INTO tool_calls (id,task_id,run_id,tool_name,tool_version,arguments,idempotency_key,state,result,error,created_at,updated_at,provider_call_id) VALUES (?,NULL,?,'procedure.draft','1',?,?,'completed',?,NULL,?,?,?)",
                (
                    str(uuid4()),
                    run_id,
                    encoded_arguments,
                    call_id,
                    json.dumps(result),
                    now,
                    now,
                    call_id,
                ),
            )
            self.runs.record_event(
                connection,
                run_id,
                "tool.intended",
                {"call_id": call_id, "tool": "procedure.draft", "internal": False},
            )
            self.runs.record_event(
                connection,
                run_id,
                "procedure.draft_created",
                {"skill_id": identity, "version": 1, "call_id": call_id},
            )
            self.runs.record_event(
                connection, run_id, "tool.completed", {"call_id": call_id, "internal": False}
            )
        return result

    def create_model_revision_from_run(
        self, run_id: str, owner: str, call_id: str, revision: SkillContentRevision
    ) -> dict[str, Any]:
        """Create a content-only candidate version under a per-run user grant."""
        if self.runs.task_scope is not None:
            raise PermissionError("Specialists cannot revise user-library procedures.")
        if not call_id or len(call_id) > 128:
            raise ValueError("Procedure revision call identity is invalid.")
        now = utc_now()
        encoded_arguments = json.dumps(revision.model_dump(mode="json"), sort_keys=True)
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, owner)
            grants = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='procedure.revision_granted'",
                (run_id,),
            ).fetchall()
            if len(grants) != 1:
                raise PermissionError("This run has no user authorization to revise procedures.")
            grant = json.loads(grants[0][0])
            selected = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='procedure.selected'",
                (run_id,),
            ).fetchall()
            if (
                grant.get("actor") != "local_user"
                or grant.get("skill_id") != revision.skill_id
                or grant.get("expected_version") != revision.expected_version
                or len(selected) != 1
                or json.loads(selected[0][0]).get("skill_id") != revision.skill_id
                or json.loads(selected[0][0]).get("version") != revision.expected_version
            ):
                raise PermissionError("Procedure revision exceeds the selected run grant.")
            if connection.execute(
                "SELECT 1 FROM tool_calls WHERE run_id=? AND provider_call_id=?",
                (run_id, call_id),
            ).fetchone():
                raise SkillConflict("The model procedure-revision call was already used.")
            entry = connection.execute(
                "SELECT name,lifecycle_state,active_version FROM skills WHERE id=?",
                (revision.skill_id,),
            ).fetchone()
            latest = connection.execute(
                "SELECT * FROM skill_versions WHERE skill_id=? ORDER BY version DESC LIMIT 1",
                (revision.skill_id,),
            ).fetchone()
            if entry is None or latest is None:
                raise SkillConflict("The selected procedure no longer exists.")
            current_version = int(latest["version"])
            if entry["lifecycle_state"] == "deprecated":
                raise SkillConflict("Deprecated procedures cannot receive model revisions.")
            if current_version != revision.expected_version:
                raise SkillConflict("Procedure changed; reload its latest version before revising.")
            if current_version >= 128:
                raise SkillConflict("A procedure cannot exceed 128 versions.")
            prior_evidence: dict[str, Any] = json.loads(latest["evaluation_evidence"])
            raw_prior_source = prior_evidence.get("source")
            prior_source: dict[str, Any] = (
                cast(dict[str, Any], raw_prior_source) if isinstance(raw_prior_source, dict) else {}
            )
            prior_assets = connection.execute(
                "SELECT relative_path,media_type,sha256,content FROM skill_version_assets "
                "WHERE skill_id=? AND version=? ORDER BY relative_path",
                (revision.skill_id, current_version),
            ).fetchall()
            draft = SkillDraft(
                name=entry["name"],
                content=revision.content,
                description=prior_evidence.get("description", ""),
                category=prior_evidence.get("category", ""),
                required_tools=json.loads(latest["required_tools"]),
                required_permissions=json.loads(latest["required_permissions"]),
                assets=[
                    SkillAsset(
                        path=asset["relative_path"],
                        media_type=asset["media_type"],
                        sha256=asset["sha256"],
                        content_base64=base64.b64encode(asset["content"]).decode("ascii"),
                    )
                    for asset in prior_assets
                ],
            )
            evidence: dict[str, Any] = {
                "state": "candidate",
                "provenance": "model_revision",
                "source": {
                    **{
                        str(key): value
                        for key, value in prior_source.items()
                        if isinstance(value, str)
                    },
                    "revision_run_id": run_id,
                    "revision_call_id": call_id,
                    "parent_version": str(current_version),
                    "parent_provenance": str(prior_evidence.get("provenance", "user")),
                },
                "evaluations": [],
            }
            next_version = current_version + 1
            self._insert_version(connection, revision.skill_id, next_version, draft, evidence)
            connection.execute(
                "UPDATE skills SET updated_at=? WHERE id=?", (now, revision.skill_id)
            )
            summary = {
                "id": revision.skill_id,
                "name": entry["name"],
                "description": draft.description,
                "category": draft.category,
                "lifecycle_state": entry["lifecycle_state"],
                "active_version": entry["active_version"],
                "latest_version": next_version,
            }
            result = {
                "status": "candidate_version_created",
                "procedure": summary,
                "version": next_version,
                "trust": "untrusted_model_revision",
                "requires_user_review": True,
                "notice": "The new version inherits the existing tools, permissions, metadata and resources. It was not evaluated or made active.",
            }
            connection.execute(
                "INSERT INTO tool_calls (id,task_id,run_id,tool_name,tool_version,arguments,idempotency_key,state,result,error,created_at,updated_at,provider_call_id) VALUES (?,NULL,?,'procedure.revise','1',?,?,'completed',?,NULL,?,?,?)",
                (
                    str(uuid4()),
                    run_id,
                    encoded_arguments,
                    call_id,
                    json.dumps(result),
                    now,
                    now,
                    call_id,
                ),
            )
            self.runs.record_event(
                connection,
                run_id,
                "tool.intended",
                {"call_id": call_id, "tool": "procedure.revise", "internal": False},
            )
            self.runs.record_event(
                connection,
                run_id,
                "procedure.version_drafted",
                {
                    "skill_id": revision.skill_id,
                    "version": next_version,
                    "parent_version": current_version,
                    "call_id": call_id,
                },
            )
            self.runs.record_event(
                connection, run_id, "tool.completed", {"call_id": call_id, "internal": False}
            )
        return result

    def import_agent_plugin(
        self,
        draft: SkillDraft,
        source: dict[str, str],
        *,
        source_kind: Literal["agent_plugin", "agent_plugins_v1", "hermes_skill"] = "agent_plugin",
    ) -> dict[str, Any]:
        """Create an inactive candidate with immutable package source provenance."""
        identity, now = str(uuid4()), utc_now()
        evidence: dict[str, Any] = {
            "state": "candidate",
            "provenance": source_kind,
            "source": source,
            "evaluations": [],
        }
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
            self._insert_version(connection, identity, 1, draft, evidence)
        return self.get(identity)

    def revise(self, identity: str, draft: SkillRevision) -> dict[str, Any]:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lifecycle_state,active_version FROM skills WHERE id=?", (identity,)
            ).fetchone()
            if row is None:
                raise KeyError(identity)
            latest_row = connection.execute(
                "SELECT version,evaluation_evidence FROM skill_versions "
                "WHERE skill_id=? ORDER BY version DESC LIMIT 1",
                (identity,),
            ).fetchone()
            if latest_row is None:
                raise RuntimeError("Procedure has no stored versions.")
            latest = int(latest_row["version"])
            if latest != draft.expected_version:
                raise SkillConflict("Procedure changed; reload its latest candidate version.")
            if not draft.assets:
                prior_assets = connection.execute(
                    "SELECT relative_path,media_type,sha256,content FROM skill_version_assets "
                    "WHERE skill_id=? AND version=? ORDER BY relative_path",
                    (identity, latest),
                ).fetchall()
                draft = draft.model_copy(
                    update={
                        "assets": [
                            SkillAsset(
                                path=item["relative_path"],
                                media_type=item["media_type"],
                                sha256=item["sha256"],
                                content_base64=base64.b64encode(item["content"]).decode("ascii"),
                            )
                            for item in prior_assets
                        ]
                    }
                )
            try:
                connection.execute(
                    "UPDATE skills SET name=?,updated_at=?,lifecycle_state=? WHERE id=?",
                    (
                        draft.name.strip(),
                        utc_now(),
                        "active" if row["active_version"] is not None else "candidate",
                        identity,
                    ),
                )
            except sqlite3.IntegrityError:
                raise SkillConflict("A procedure with that name already exists.") from None
            prior_evidence: dict[str, Any] = json.loads(str(latest_row["evaluation_evidence"]))
            if not draft.description or not draft.category:
                draft = draft.model_copy(
                    update={
                        "description": draft.description or prior_evidence.get("description", ""),
                        "category": draft.category or prior_evidence.get("category", ""),
                    }
                )
            evidence: dict[str, Any] = {
                "state": "candidate",
                "provenance": prior_evidence.get("provenance", "user"),
                "evaluations": [],
            }
            if "source" in prior_evidence:
                evidence["source"] = prior_evidence["source"]
            self._insert_version(connection, identity, latest + 1, draft, evidence)
        return self.get(identity)

    def list(self) -> list[dict[str, Any]]:
        with self.runs.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT skills.*,MAX(skill_versions.version) AS latest_version,(SELECT COALESCE(MAX(sequence),0) FROM skill_lifecycle_events WHERE skill_id=skills.id) AS lifecycle_revision,(SELECT COALESCE(json_extract(v.evaluation_evidence,'$.description'),'') FROM skill_versions v WHERE v.skill_id=skills.id AND v.version=COALESCE(skills.active_version,(SELECT MAX(active.version) FROM skill_versions active WHERE active.skill_id=skills.id))) AS description,(SELECT COALESCE(json_extract(v.evaluation_evidence,'$.category'),'') FROM skill_versions v WHERE v.skill_id=skills.id AND v.version=COALESCE(skills.active_version,(SELECT MAX(active.version) FROM skill_versions active WHERE active.skill_id=skills.id))) AS category FROM skills JOIN skill_versions ON skills.id=skill_versions.skill_id GROUP BY skills.id ORDER BY skills.updated_at DESC,skills.id LIMIT 100"
                )
            ]

    def catalog(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
        available_tools: set[str] | None = None,
        toolsets: set[str] | None = None,
    ) -> dict[str, Any]:
        """Return bounded discovery metadata; procedure text is never included."""
        if not 1 <= limit <= 50 or not 0 <= offset <= 99:
            raise ValueError("Procedure catalog pagination is out of range.")
        if category is not None and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", category):
            raise ValueError("Procedure category must be a lowercase slug.")
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT skills.id,skills.name,skills.lifecycle_state,skills.active_version,"
                "(SELECT MAX(v.version) FROM skill_versions v WHERE v.skill_id=skills.id) AS latest_version,skill_versions.content,"
                "skill_versions.required_tools,skill_versions.required_permissions,"
                "skill_versions.evaluation_evidence FROM skills JOIN skill_versions "
                "ON skills.id=skill_versions.skill_id AND skill_versions.version="
                "COALESCE(skills.active_version,(SELECT MAX(v.version) FROM skill_versions v "
                "WHERE v.skill_id=skills.id)) WHERE skills.lifecycle_state!='deprecated' "
                "ORDER BY skills.name COLLATE NOCASE,skills.id LIMIT 100"
            ).fetchall()
        procedures: list[dict[str, Any]] = []
        for row in rows:
            evidence = cast(dict[str, Any], json.loads(row["evaluation_evidence"]))
            raw_source: Any = evidence.get("source", {})
            source = cast(dict[str, Any], raw_source) if isinstance(raw_source, dict) else {}
            if available_tools is not None and toolsets is not None:
                raw_requirements: Any = source.get("declared_requirements")
                if isinstance(raw_requirements, str) and len(raw_requirements) <= 8192:
                    try:
                        decoded_requirements: Any = json.loads(raw_requirements)
                    except json.JSONDecodeError:
                        decoded_requirements = {}
                    if isinstance(
                        decoded_requirements, dict
                    ) and not procedure_matches_tool_conditions(
                        cast(dict[str, Any], decoded_requirements),
                        available_tools=available_tools,
                        toolsets=toolsets,
                    ):
                        continue
            description = str(evidence.get("description") or source.get("description", "")).strip()
            if not description:
                description = "User-authored saved procedure."
            raw_category: Any = evidence.get("category") or source.get("category", "")
            procedure_category = raw_category if isinstance(raw_category, str) else ""
            if category is not None and procedure_category != category:
                continue
            procedures.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": description[:256],
                    "category": procedure_category,
                    "lifecycle_state": row["lifecycle_state"],
                    "active_version": row["active_version"],
                    "latest_version": row["latest_version"],
                    "required_tools": json.loads(row["required_tools"]),
                    "required_permissions": json.loads(row["required_permissions"]),
                }
            )
        page = procedures[offset : offset + limit]
        next_offset = offset + len(page) if offset + len(page) < len(procedures) else None
        return {
            "procedures": page,
            "next_offset": next_offset,
            "selection": "explicit_user_only",
            "trust": "untrusted_procedure_metadata",
        }

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
        value["description"] = value["evaluation_evidence"].pop("description", "")
        value["category"] = value["evaluation_evidence"].pop("category", "")
        with self.runs.connection() as connection:
            assets: list[sqlite3.Row] = connection.execute(
                "SELECT relative_path,media_type,sha256,content FROM skill_version_assets "
                "WHERE skill_id=? AND version=? ORDER BY relative_path",
                (identity, version),
            ).fetchall()
        value["assets"] = [
            {
                "path": item["relative_path"],
                "media_type": item["media_type"],
                "sha256": item["sha256"],
                "content_base64": base64.b64encode(item["content"]).decode("ascii"),
            }
            for item in assets
        ]
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
            if evidence.get("description"):
                manifest["description"] = evidence["description"]
            if evidence.get("category"):
                manifest["category"] = evidence["category"]
            asset_rows = connection.execute(
                "SELECT relative_path,media_type,sha256 FROM skill_version_assets "
                "WHERE skill_id=? AND version=? ORDER BY relative_path",
                (identity, version),
            ).fetchall()
            if asset_rows:
                manifest["assets"] = [
                    {
                        "path": item["relative_path"],
                        "media_type": item["media_type"],
                        "sha256": item["sha256"],
                    }
                    for item in asset_rows
                ]
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
                    "UPDATE skills SET lifecycle_state='evaluated',updated_at=? WHERE id=? AND active_version IS NULL AND lifecycle_state!='deprecated'",
                    (utc_now(), identity),
                )
        return self.version(identity, version)

    def lifecycle(self, identity: str) -> builtins.list[dict[str, Any]]:
        self.get(identity)
        with self.runs.connection() as connection:
            return [
                {
                    "sequence": row["sequence"],
                    "created_at": row["created_at"],
                    **json.loads(row["payload"]),
                }
                for row in connection.execute(
                    "SELECT * FROM skill_lifecycle_events WHERE skill_id=? ORDER BY sequence",
                    (identity,),
                )
            ]

    def decide_lifecycle(self, identity: str, decision: SkillLifecycleDecision) -> dict[str, Any]:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM skills WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError(identity)
            latest = connection.execute(
                "SELECT MAX(version) FROM skill_versions WHERE skill_id=?", (identity,)
            ).fetchone()[0]
            revision = connection.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM skill_lifecycle_events WHERE skill_id=?",
                (identity,),
            ).fetchone()[0]
            current = row["active_version"]
            if (current, latest, revision) != (
                decision.expected_active_version,
                decision.expected_latest_version,
                decision.expected_lifecycle_revision,
            ):
                raise SkillConflict("Procedure changed; reload before deciding its lifecycle.")
            digest = None
            target = decision.target_version
            if decision.action == "deprecate":
                if row["lifecycle_state"] == "deprecated":
                    raise SkillConflict("Procedure is already deprecated.")
                state = "deprecated"
            else:
                selected = connection.execute(
                    "SELECT * FROM skill_versions WHERE skill_id=? AND version=?",
                    (identity, target),
                ).fetchone()
                if selected is None:
                    raise SkillConflict("Target procedure version does not exist.")
                evidence = json.loads(selected["evaluation_evidence"])
                manifest = {
                    key: json.loads(selected[key]) if key != "content" else selected[key]
                    for key in ("content", "required_tools", "required_permissions")
                }
                if evidence.get("description"):
                    manifest["description"] = evidence["description"]
                if evidence.get("category"):
                    manifest["category"] = evidence["category"]
                asset_rows = connection.execute(
                    "SELECT relative_path,media_type,sha256 FROM skill_version_assets "
                    "WHERE skill_id=? AND version=? ORDER BY relative_path",
                    (identity, target),
                ).fetchall()
                if asset_rows:
                    manifest["assets"] = [
                        {
                            "path": item["relative_path"],
                            "media_type": item["media_type"],
                            "sha256": item["sha256"],
                        }
                        for item in asset_rows
                    ]
                manifest["required_permissions"] = sorted(manifest["required_permissions"])
                digest = snapshot_digest(manifest)
                if evidence.get("state") != "evaluated" or not any(
                    record.get("snapshot_sha256") == digest
                    for record in evidence.get("evaluations", [])
                ):
                    raise SkillConflict("Only an explicitly evaluated version can become active.")
                assert target is not None
                if decision.action == "rollback":
                    if current is None or target >= current:
                        raise SkillConflict(
                            "Rollback requires an older evaluated version and a current active version."
                        )
                elif current is not None and target <= current:
                    raise SkillConflict(
                        "Use rollback to select an older version; this version may already be active."
                    )
                state = "active"
            connection.execute(
                "UPDATE skills SET active_version=?,lifecycle_state=?,updated_at=? WHERE id=?",
                (target, state, utc_now(), identity),
            )
            payload = {
                "action": decision.action,
                "actor": "local-user",
                "from_version": current,
                "to_version": target,
                "rationale": decision.rationale,
                "snapshot_sha256": digest,
            }
            connection.execute(
                "INSERT INTO skill_lifecycle_events VALUES (?,?,?,?)",
                (identity, revision + 1, json.dumps(payload), utc_now()),
            )
        return self.get(identity)

    def delete(self, identity: str) -> None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT active_version FROM skills WHERE id=?", (identity,)
            ).fetchone()
            if active is not None and active[0] is not None:
                raise SkillConflict("Deprecate the procedure before deleting its active version.")
            if (
                connection.execute(
                    "DELETE FROM skills WHERE id=? AND active_version IS NULL",
                    (identity,),
                ).rowcount
                != 1
            ):
                raise KeyError(identity)

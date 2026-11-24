from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore
from xaa.storage.skills import (
    SkillAsset,
    SkillConflict,
    SkillContentRevision,
    SkillDraft,
    SkillResourceRemoval,
    SkillStore,
    SkillTool,
)

API_TOKEN = "a-procedure-draft-api-token-long-enough-to-pass"


def _draft(name: str = "Model candidate") -> SkillDraft:
    return SkillDraft(
        name=name,
        description="Read a bounded project configuration safely.",
        category="project-review",
        content="Read the selected configuration and report only the requested fields.",
        required_tools=[SkillTool(name="workspace.read_file", version="1")],
        required_permissions=["workspace.read"],
    )


def _run(store: RunStore, *, authorized: bool = True) -> tuple[str, str]:
    run = store.create_run(
        objective="Draft a reusable procedure from verified work.",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        allow_procedure_drafting=authorized,
    )
    run_id = str(run["id"])
    owner = store.acquire_lease(run_id, "procedure-draft-test")
    assert owner
    return run_id, owner


def test_model_draft_is_inactive_and_tool_journal_is_atomic(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft.sqlite3")
    run_id, owner = _run(store)

    result = SkillStore(store).create_model_draft_from_run(run_id, owner, "draft-1", _draft())
    procedure = result["procedure"]

    assert result["status"] == "candidate_created"
    assert result["trust"] == "untrusted_model_draft"
    assert result["requires_user_review"] is True
    assert procedure["lifecycle_state"] == "candidate"
    assert procedure["active_version"] is None
    assert procedure["latest_version"] == 1
    saved_run = store.get_run(run_id)
    assert saved_run is not None
    assert saved_run["procedure_trial"] is None
    saved = SkillStore(store).version(procedure["id"], 1)
    assert saved["evaluation_evidence"]["state"] == "candidate"
    assert saved["evaluation_evidence"]["provenance"] == "model_draft"
    assert saved["evaluation_evidence"]["source"] == {"run_id": run_id, "call_id": "draft-1"}

    with store.connection() as connection:
        call = connection.execute(
            "SELECT state,result FROM tool_calls WHERE run_id=? AND provider_call_id='draft-1'",
            (run_id,),
        ).fetchone()
        event_types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM run_events WHERE run_id=? ORDER BY sequence", (run_id,)
            )
        ]
    assert call is not None
    assert call["state"] == "completed"
    assert json.loads(call["result"]) == result
    assert event_types[-3:] == ["tool.intended", "procedure.draft_created", "tool.completed"]


def test_model_draft_batch_saves_candidates_and_journals_atomically(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-batch.sqlite3")
    run_id, owner = _run(store)

    results = SkillStore(store).create_model_draft_batch_from_run(
        run_id, owner, [("draft-1", _draft("Candidate one")), ("draft-2", _draft("Candidate two"))]
    )

    assert [item["procedure"]["name"] for item in results] == ["Candidate one", "Candidate two"]
    assert all(item["procedure"]["active_version"] is None for item in results)
    with store.connection() as connection:
        calls = connection.execute(
            "SELECT provider_call_id,state FROM tool_calls WHERE run_id=? ORDER BY provider_call_id",
            (run_id,),
        ).fetchall()
        created = connection.execute(
            "SELECT COUNT(*) FROM run_events WHERE run_id=? AND event_type='procedure.draft_created'",
            (run_id,),
        ).fetchone()[0]
    assert [(row["provider_call_id"], row["state"]) for row in calls] == [
        ("draft-1", "completed"),
        ("draft-2", "completed"),
    ]
    assert created == 2
    assert len(SkillStore(store).list()) == 2
    assert store.unresolved_tool_calls(run_id) == []


def test_model_draft_batch_conflict_creates_no_partial_candidates(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-batch-conflict.sqlite3")
    run_id, owner = _run(store)

    with pytest.raises(SkillConflict, match="cannot repeat a name"):
        SkillStore(store).create_model_draft_batch_from_run(
            run_id, owner, [("draft-1", _draft("Duplicate")), ("draft-2", _draft("duplicate"))]
        )

    assert SkillStore(store).list() == []
    assert store.unresolved_tool_calls(run_id) == []


def test_model_draft_batch_rolls_back_every_candidate_and_call_on_late_failure(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-batch-rollback.sqlite3")
    run_id, owner = _run(store)
    original_insert = SkillStore._insert_version
    inserts = 0

    def fail_second_insert(*args: Any, **kwargs: Any) -> None:
        nonlocal inserts
        inserts += 1
        if inserts == 2:
            raise RuntimeError("fixture interruption")
        original_insert(*args, **kwargs)

    monkeypatch.setattr(SkillStore, "_insert_version", staticmethod(fail_second_insert))
    with pytest.raises(RuntimeError, match="fixture interruption"):
        SkillStore(store).create_model_draft_batch_from_run(
            run_id,
            owner,
            [("draft-1", _draft("Candidate one")), ("draft-2", _draft("Candidate two"))],
        )

    assert SkillStore(store).list() == []
    assert store.unresolved_tool_calls(run_id) == []
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM tool_calls WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


def test_mixed_draft_revision_transaction_rolls_back_draft_on_revision_denial(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-revision-rollback.sqlite3")
    skills = SkillStore(store)
    selected = skills.create(_draft("Existing procedure"))
    run = store.create_run(
        objective="Create a candidate and revise the selected procedure.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": selected["id"], "version": 1},
        allow_procedure_drafting=True,
        allow_procedure_revision=True,
    )
    run_id = str(run["id"])
    owner = store.acquire_lease(run_id, "mixed-procedure-rollback")
    assert owner

    with pytest.raises(PermissionError, match="exceeds the selected run grant"):
        skills.create_model_draft_and_edit_batch_from_run(
            run_id,
            owner,
            [("draft-call", _draft("New candidate"))],
            "revision-call",
            [("revision-call", "procedure_revise", {"expected_version": 2})],
            [("revision-call", {"expected_version": 2})],
            content_revision=SkillContentRevision(
                skill_id=str(selected["id"]), expected_version=2, content="Revised content."
            ),
        )

    assert [procedure["name"] for procedure in skills.list()] == ["Existing procedure"]
    assert skills.get(str(selected["id"]))["latest_version"] == 1
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM tool_calls WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )


def test_mixed_draft_and_resource_removal_commit_in_one_transaction(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-removal.sqlite3")
    skills = SkillStore(store)
    original = _draft("Existing procedure")
    content = b"obsolete reference"
    original.assets = [
        SkillAsset(
            path="references/obsolete.txt",
            media_type="text/plain",
            sha256=hashlib.sha256(content).hexdigest(),
            content_base64=base64.b64encode(content).decode("ascii"),
        )
    ]
    selected = skills.create(original)
    run = store.create_run(
        objective="Draft a candidate and remove an obsolete selected-procedure resource.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": selected["id"], "version": 1},
        allow_procedure_drafting=True,
        allow_procedure_resource_removal=True,
    )
    run_id = str(run["id"])
    owner = store.acquire_lease(run_id, "mixed-draft-resource-removal")
    assert owner
    removal = SkillResourceRemoval(
        skill_id=str(selected["id"]), expected_version=1, path="references/obsolete.txt"
    )

    results = skills.create_model_draft_and_edit_batch_from_run(
        run_id,
        owner,
        [("draft-call", _draft("New candidate"))],
        "remove-call",
        [("remove-call", "procedure_resource_remove", removal.model_dump(mode="json"))],
        [],
        resource_removal=removal,
    )

    assert set(results) == {"draft-call", "remove-call"}
    assert results["draft-call"]["status"] == "candidate_created"
    assert results["remove-call"]["removed_resource_path"] == "references/obsolete.txt"
    assert skills.version(str(selected["id"]), 2)["assets"] == []
    assert {item["name"] for item in skills.list()} == {"Existing procedure", "New candidate"}
    with store.connection() as connection:
        calls = connection.execute(
            "SELECT provider_call_id,state FROM tool_calls WHERE run_id=? ORDER BY provider_call_id",
            (run_id,),
        ).fetchall()
    assert [(row["provider_call_id"], row["state"]) for row in calls] == [
        ("draft-call", "completed"),
        ("remove-call", "completed"),
    ]


def test_rejected_tool_batch_failure_journal_rolls_back_as_a_unit(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-failed-batch-rollback.sqlite3")
    run_id, owner = _run(store)
    original_record_event = RunStore.record_event
    events = 0

    def fail_late_event(
        connection: Any, event_run_id: str, kind: str, payload: dict[str, Any]
    ) -> None:
        nonlocal events
        events += 1
        if events == 3:
            raise RuntimeError("fixture interruption")
        original_record_event(connection, event_run_id, kind, payload)

    monkeypatch.setattr(RunStore, "record_event", staticmethod(fail_late_event))
    with pytest.raises(RuntimeError, match="fixture interruption"):
        store.record_tool_failure_batch(
            run_id,
            owner,
            [
                ("failed-call-1", "procedure.draft", "{}"),
                ("failed-call-2", "procedure.revise", "{}"),
            ],
            code="incompatible_procedure_manager_batch",
            message="Separate procedure creation and edits.",
        )

    assert store.unresolved_tool_calls(run_id) == []
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM tool_calls WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM run_events WHERE run_id=? AND event_type IN ('tool.intended','tool.failed')",
                (run_id,),
            ).fetchone()[0]
            == 0
        )


def test_unapproved_run_cannot_create_model_draft(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-denied.sqlite3")
    run_id, owner = _run(store, authorized=False)

    with pytest.raises(PermissionError, match="no user authorization"):
        SkillStore(store).create_model_draft_from_run(run_id, owner, "draft-1", _draft())
    assert SkillStore(store).list() == []
    assert store.unresolved_tool_calls(run_id) == []


def test_api_persists_explicit_model_drafting_grant(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-api.sqlite3")
    app = create_app(LMStudioProvider(), SecretStr(API_TOKEN), store=store)
    api_client: Any = TestClient(app)
    with api_client as client:
        response: Any = client.post(
            "/runs",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            json={
                "objective": "Draft a reusable procedure.",
                "allow_procedure_drafting": True,
            },
        )
    assert response.status_code == 201
    run: dict[str, Any] = response.json()
    assert run["allow_procedure_drafting"] is True
    events = store.list_events(str(run["id"]))
    assert [event["event_type"] for event in events].count("procedure.drafting_granted") == 1


def test_duplicate_name_conflict_leaves_no_candidate_or_tool_call(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-conflict.sqlite3")
    SkillStore(store).create(_draft())
    run_id, owner = _run(store)

    with pytest.raises(SkillConflict, match="already exists"):
        SkillStore(store).create_model_draft_from_run(run_id, owner, "draft-1", _draft())
    assert len(SkillStore(store).list()) == 1
    assert store.unresolved_tool_calls(run_id) == []


def test_failed_transaction_rolls_back_candidate_and_journal(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-rollback.sqlite3")
    run_id, owner = _run(store)

    def fail_insert(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("fixture interruption")

    monkeypatch.setattr(SkillStore, "_insert_version", staticmethod(fail_insert))
    with pytest.raises(RuntimeError, match="fixture interruption"):
        SkillStore(store).create_model_draft_from_run(run_id, owner, "draft-1", _draft())

    assert SkillStore(store).list() == []
    assert store.unresolved_tool_calls(run_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [True, False])
async def test_coordinator_exposes_draft_tool_only_after_explicit_run_grant(
    workspace_tmp_path: Path, authorized: bool
) -> None:
    store = RunStore(workspace_tmp_path / f"procedure-draft-coordinator-{authorized}.sqlite3")
    run_id = str(
        store.create_run(
            objective="Draft a reusable procedure from verified work.",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model="fixture",
            allow_procedure_drafting=authorized,
        )["id"]
    )
    draft_call = {
        "id": "draft-call-1",
        "type": "function",
        "function": {
            "name": "procedure_draft",
            "arguments": json.dumps(_draft().model_dump(mode="json")),
        },
    }

    class DraftProvider(LMStudioProvider):
        requests = 0

        async def diagnose_tools(self, model: str, *, request: Any = None) -> bool:
            return True

        async def chat(
            self,
            *,
            model: str,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            self.requests += 1
            available = {item["function"]["name"] for item in (tools or [])}
            if authorized and self.requests == 1:
                assert "procedure_draft" in available
                return {"choices": [{"message": {"role": "assistant", "tool_calls": [draft_call]}}]}
            if not authorized:
                assert "procedure_draft" not in available
            else:
                assert "procedure_draft" in available
            return {"choices": [{"message": {"role": "assistant", "content": "No draft created."}}]}

    provider = DraftProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()

    procedures = SkillStore(store).list()
    assert len(procedures) == (1 if authorized else 0)
    if authorized:
        assert procedures[0]["lifecycle_state"] == "candidate"
        assert procedures[0]["active_version"] is None
        assert not store.unresolved_tool_calls(run_id)


@pytest.mark.asyncio
async def test_coordinator_commits_same_response_draft_batch_as_one_storage_unit(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-coordinator-batch.sqlite3")
    run_id = str(
        store.create_run(
            objective="Draft two independent procedures from verified work.",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model="fixture",
            allow_procedure_drafting=True,
        )["id"]
    )
    calls = [
        {
            "id": f"draft-call-{index}",
            "type": "function",
            "function": {
                "name": "procedure_draft",
                "arguments": json.dumps(_draft(f"Candidate {index}").model_dump(mode="json")),
            },
        }
        for index in (1, 2)
    ]

    class DraftBatchProvider(LMStudioProvider):
        requests = 0

        async def diagnose_tools(self, model: str, *, request: Any = None) -> bool:
            return True

        async def chat(
            self,
            *,
            model: str,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            self.requests += 1
            if self.requests == 1:
                assert "procedure_draft" in {item["function"]["name"] for item in (tools or [])}
                return {"choices": [{"message": {"role": "assistant", "tool_calls": calls}}]}
            return {
                "choices": [{"message": {"role": "assistant", "content": "Two candidates saved."}}]
            }

    provider = DraftBatchProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()

    procedures = SkillStore(store).list()
    assert {item["name"] for item in procedures} == {"Candidate 1", "Candidate 2"}
    assert all(item["lifecycle_state"] == "candidate" for item in procedures)
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.asyncio
async def test_coordinator_commits_mixed_draft_and_edit_batch_atomically(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-draft-mixed-batch.sqlite3")
    skills = SkillStore(store)
    selected = skills.create(_draft("Selected procedure"))
    run_id = str(
        store.create_run(
            objective="Draft a new procedure and revise the selected one.",
            acceptance_criteria=[],
            workspace=".",
            mode="observe",
            model="fixture",
            procedure_trial={"skill_id": selected["id"], "version": 1},
            allow_procedure_drafting=True,
            allow_procedure_revision=True,
            allow_procedure_resource_revision=True,
        )["id"]
    )
    calls = [
        {
            "id": "mixed-draft-call",
            "type": "function",
            "function": {
                "name": "procedure_draft",
                "arguments": json.dumps(_draft("New candidate").model_dump(mode="json")),
            },
        },
        {
            "id": "mixed-revision-call",
            "type": "function",
            "function": {
                "name": "procedure_revise",
                "arguments": json.dumps(
                    {
                        "skill_id": selected["id"],
                        "expected_version": 1,
                        "old_string": "Read the selected configuration",
                        "new_string": "Read the approved configuration",
                    }
                ),
            },
        },
        {
            "id": "mixed-resource-call",
            "type": "function",
            "function": {
                "name": "procedure_resource_revise",
                "arguments": json.dumps(
                    {
                        "skill_id": selected["id"],
                        "expected_version": 1,
                        "path": "references/current.txt",
                        "content": "Current reference.",
                    }
                ),
            },
        },
    ]

    class MixedManagerProvider(LMStudioProvider):
        requests = 0

        async def diagnose_tools(self, model: str, *, request: Any = None) -> bool:
            return True

        async def chat(
            self,
            *,
            model: str,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            self.requests += 1
            if self.requests == 1:
                available = {item["function"]["name"] for item in (tools or [])}
                assert {
                    "procedure_draft",
                    "procedure_revise",
                    "procedure_resource_revise",
                } <= available
                return {"choices": [{"message": {"role": "assistant", "tool_calls": calls}}]}
            return {"choices": [{"message": {"role": "assistant", "content": "Batch committed."}}]}

    provider = MixedManagerProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()

    assert len(skills.list()) == 2
    assert skills.get(selected["id"])["latest_version"] == 2
    assert "approved configuration" in skills.version(selected["id"], 2)["content"]
    assert skills.version(selected["id"], 2)["assets"][0]["path"] == "references/current.txt"
    assert any(skill["name"] == "New candidate" for skill in skills.list())
    with store.connection() as connection:
        failures = connection.execute(
            "SELECT provider_call_id,state,error FROM tool_calls WHERE run_id=? ORDER BY provider_call_id",
            (run_id,),
        ).fetchall()
    assert [(row["provider_call_id"], row["state"]) for row in failures] == [
        ("mixed-draft-call", "completed"),
        ("mixed-resource-call", "completed"),
        ("mixed-revision-call", "completed"),
    ]
    assert all(row["error"] is None for row in failures)
    assert not store.unresolved_tool_calls(run_id)

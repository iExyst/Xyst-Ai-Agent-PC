from __future__ import annotations

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
from xaa.storage.skills import SkillConflict, SkillDraft, SkillStore, SkillTool

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

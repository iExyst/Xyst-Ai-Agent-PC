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
    SkillRevision,
    SkillStore,
    SkillTool,
)

API_TOKEN = "a-procedure-revision-api-token-long-enough-to-pass"


def _draft(name: str = "Selected procedure") -> SkillDraft:
    return SkillDraft(
        name=name,
        description="Inspect the selected project safely.",
        category="inspection",
        content="Read requested files and report observed facts.",
        required_tools=[SkillTool(name="workspace.read_file", version="1")],
        required_permissions=["workspace.read"],
    )


def _run(
    store: RunStore,
    skill_id: str,
    *,
    version: int = 1,
    authorized: bool = True,
) -> tuple[str, str]:
    run = store.create_run(
        objective="Improve the selected procedure based on this run.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": skill_id, "version": version},
        allow_procedure_revision=authorized,
    )
    run_id = str(run["id"])
    owner = store.acquire_lease(run_id, "procedure-revision-test")
    assert owner
    return run_id, owner


def _revision(skill_id: str, version: int = 1) -> SkillContentRevision:
    return SkillContentRevision(
        skill_id=skill_id,
        expected_version=version,
        content="Read only the requested files, cite each path, and ask before edits.",
    )


def _targeted_patch(
    skill_id: str, old_string: str, new_string: str, *, replace_all: bool = False
) -> SkillContentRevision:
    return SkillContentRevision(
        skill_id=skill_id,
        expected_version=1,
        old_string=old_string,
        new_string=new_string,
        replace_all=replace_all,
    )


def test_model_revision_creates_content_only_candidate_version(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-revision.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    run_id, owner = _run(store, entry["id"])

    result = skills.create_model_revision_from_run(
        run_id, owner, "revise-1", _revision(entry["id"])
    )

    before = skills.version(entry["id"], 1)
    after = skills.version(entry["id"], 2)
    current = skills.get(entry["id"])
    assert result["status"] == "candidate_version_created"
    assert result["requires_user_review"] is True
    assert before["content"] == "Read requested files and report observed facts."
    assert after["content"] == _revision(entry["id"]).content
    assert after["required_tools"] == before["required_tools"]
    assert after["required_permissions"] == before["required_permissions"]
    assert after["evaluation_evidence"]["state"] == "candidate"
    assert after["evaluation_evidence"]["provenance"] == "model_revision"
    assert after["evaluation_evidence"]["evaluations"] == []
    assert after["evaluation_evidence"]["source"]["parent_version"] == "1"
    assert current["latest_version"] == 2
    assert current["active_version"] is None
    assert not store.unresolved_tool_calls(run_id)
    with store.connection() as connection:
        call = connection.execute(
            "SELECT state FROM tool_calls WHERE run_id=? AND provider_call_id='revise-1'",
            (run_id,),
        ).fetchone()
    assert call is not None and call["state"] == "completed"


def test_model_revision_supports_exact_targeted_text_replacement(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-revision-targeted.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    run_id, owner = _run(store, entry["id"])

    result = skills.create_model_revision_from_run(
        run_id,
        owner,
        "targeted-revision",
        _targeted_patch(entry["id"], "requested files", "assigned files"),
    )

    assert result["edit_mode"] == "targeted_replace"
    assert result["replacements"] == 1
    assert (
        skills.version(entry["id"], 1)["content"]
        == "Read requested files and report observed facts."
    )
    assert (
        skills.version(entry["id"], 2)["content"]
        == "Read assigned files and report observed facts."
    )


def test_targeted_patch_requires_unique_match_or_explicit_replace_all(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-revision-replace-all.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(
        SkillDraft(
            **{
                **_draft().model_dump(),
                "content": "Read requested files and read requested files again.",
            }
        )
    )
    run_id, owner = _run(store, entry["id"])
    with pytest.raises(SkillConflict, match="more than once"):
        skills.create_model_revision_from_run(
            run_id,
            owner,
            "ambiguous-patch",
            _targeted_patch(entry["id"], "requested files", "assigned files"),
        )

    replace_run, replace_owner = _run(store, entry["id"])
    result = skills.create_model_revision_from_run(
        replace_run,
        replace_owner,
        "replace-all-patch",
        _targeted_patch(entry["id"], "requested files", "assigned files", replace_all=True),
    )
    assert result["replacements"] == 2
    assert (
        skills.version(entry["id"], 2)["content"]
        == "Read assigned files and read assigned files again."
    )


def test_model_revision_of_active_entry_does_not_move_active_pointer(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-revision-active.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    with store.connection() as connection:
        connection.execute(
            "UPDATE skills SET lifecycle_state='active',active_version=1 WHERE id=?", (entry["id"],)
        )
    run_id, owner = _run(store, entry["id"])

    skills.create_model_revision_from_run(run_id, owner, "active-revise", _revision(entry["id"]))

    current = skills.get(entry["id"])
    assert current["lifecycle_state"] == "active"
    assert current["active_version"] == 1
    assert current["latest_version"] == 2
    assert (
        skills.version(entry["id"], 1)["content"]
        == "Read requested files and report observed facts."
    )
    assert skills.version(entry["id"], 2)["evaluation_evidence"]["state"] == "candidate"


def test_model_revision_input_cannot_change_permissions_or_dependencies() -> None:
    with pytest.raises(ValueError):
        SkillContentRevision.model_validate(
            {
                "skill_id": "selected-procedure",
                "expected_version": 1,
                "content": "Revised text.",
                "required_permissions": ["workspace.write"],
            }
        )


def test_model_revision_preserves_selected_linked_resources(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-revision-assets.sqlite3")
    resource = b"Bounded procedure reference."
    asset = SkillAsset(
        path="references/guide.txt",
        content_base64=base64.b64encode(resource).decode("ascii"),
        media_type="text/plain",
        sha256=hashlib.sha256(resource).hexdigest(),
    )
    draft = SkillDraft(**{**_draft().model_dump(), "assets": [asset]})
    skills = SkillStore(store)
    entry = skills.create(draft)
    run_id, owner = _run(store, entry["id"])

    skills.create_model_revision_from_run(run_id, owner, "asset-revise", _revision(entry["id"]))

    assert skills.version(entry["id"], 2)["assets"] == skills.version(entry["id"], 1)["assets"]
    assert (
        base64.b64decode(skills.version(entry["id"], 2)["assets"][0]["content_base64"]) == resource
    )


def test_revision_requires_grant_and_exact_selected_version(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-revision-grant.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    denied_run, denied_owner = _run(store, entry["id"], authorized=False)
    with pytest.raises(PermissionError, match="no user authorization"):
        skills.create_model_revision_from_run(
            denied_run, denied_owner, "denied", _revision(entry["id"])
        )
    other = skills.create(_draft("Other procedure"))
    run_id, owner = _run(store, entry["id"])
    with pytest.raises(PermissionError, match="selected run grant"):
        skills.create_model_revision_from_run(run_id, owner, "wrong-target", _revision(other["id"]))
    assert skills.get(entry["id"])["latest_version"] == 1
    assert skills.get(other["id"])["latest_version"] == 1
    assert store.unresolved_tool_calls(run_id) == []


def test_revision_conflict_and_failed_insert_leave_no_partial_version_or_call(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-revision-conflict.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    run_id, owner = _run(store, entry["id"])
    skills.revise(
        entry["id"],
        SkillRevision(
            name="Selected procedure",
            description="Inspect the selected project safely.",
            category="inspection",
            required_tools=[SkillTool(name="workspace.read_file", version="1")],
            required_permissions=["workspace.read"],
            assets=[],
            expected_version=1,
            content="User-authored concurrent update.",
        ),
    )
    with pytest.raises(SkillConflict, match="changed"):
        skills.create_model_revision_from_run(run_id, owner, "stale", _revision(entry["id"]))

    other_run, other_owner = _run(store, entry["id"], version=2)

    def fail_insert(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("fixture interruption")

    monkeypatch.setattr(SkillStore, "_insert_version", staticmethod(fail_insert))
    with pytest.raises(RuntimeError, match="fixture interruption"):
        skills.create_model_revision_from_run(
            other_run, other_owner, "interrupted", _revision(entry["id"], 2)
        )
    assert skills.get(entry["id"])["latest_version"] == 2
    assert not store.unresolved_tool_calls(other_run)


@pytest.mark.asyncio
async def test_coordinator_exposes_and_executes_revision_only_for_explicit_grant(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-revision-coordinator.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    run = store.create_run(
        objective="Improve the selected procedure.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": entry["id"], "version": 1},
        allow_procedure_revision=True,
    )

    class RevisionProvider(LMStudioProvider):
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
            available = {tool["function"]["name"]: tool for tool in (tools or [])}
            assert "procedure_revise" in available
            schema = available["procedure_revise"]["function"]["parameters"]
            assert schema["properties"]["skill_id"]["enum"] == [entry["id"]]
            assert schema["properties"]["expected_version"]["enum"] == [1]
            call = {
                "id": "revision-call-1",
                "type": "function",
                "function": {
                    "name": "procedure_revise",
                    "arguments": json.dumps(_revision(entry["id"]).model_dump(mode="json")),
                },
            }
            if not any(message.get("role") == "tool" for message in messages):
                return {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}
            return {
                "choices": [{"message": {"role": "assistant", "content": "Draft version saved."}}]
            }

    provider = RevisionProvider()
    try:
        await Coordinator(store, provider).execute(str(run["id"]))
    finally:
        await provider.close()
    revised = skills.version(entry["id"], 2)
    assert revised["content"] == _revision(entry["id"]).content
    assert revised["evaluation_evidence"]["state"] == "candidate"
    assert not store.unresolved_tool_calls(str(run["id"]))


def test_api_persists_content_revision_grant_for_selected_latest_version(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-revision-api.sqlite3")
    entry = SkillStore(store).create(_draft())
    app = create_app(LMStudioProvider(), SecretStr(API_TOKEN), store=store)
    api_client: Any = TestClient(app)
    with api_client as client:
        response: Any = client.post(
            "/runs",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            json={
                "objective": "Improve the selected procedure.",
                "workspace": ".",
                "procedure_trial": {"skill_id": entry["id"], "version": 1},
                "allow_procedure_revision": True,
            },
        )
    assert response.status_code == 201
    run: dict[str, Any] = response.json()
    assert run["allow_procedure_revision"] is True
    grants = [
        event
        for event in store.list_events(run["id"])
        if event["event_type"] == "procedure.revision_granted"
    ]
    assert len(grants) == 1
    assert grants[0]["payload"]["skill_id"] == entry["id"]
    assert grants[0]["payload"]["expected_version"] == 1

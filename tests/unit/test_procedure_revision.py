from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
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
    SkillResourceRevision,
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


def _resource_revision_run(
    store: RunStore,
    skill_id: str,
    version: int = 1,
    *,
    removal_authorized: bool = False,
) -> tuple[str, str]:
    run = store.create_run(
        objective="Improve one linked procedure resource.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": skill_id, "version": version},
        allow_procedure_resource_revision=True,
        allow_procedure_resource_removal=removal_authorized,
    )
    owner = store.acquire_lease(str(run["id"]), "procedure-resource-test")
    assert owner is not None
    return str(run["id"]), owner


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


def test_model_resource_revision_creates_inactive_version_and_preserves_other_resources(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-revision.sqlite3")
    skills = SkillStore(store)
    original = _draft()
    original.assets = [
        SkillAsset(
            path="references/guide.md",
            media_type="text/markdown",
            sha256=hashlib.sha256(b"Old guide.").hexdigest(),
            content_base64=base64.b64encode(b"Old guide.").decode("ascii"),
        ),
        SkillAsset(
            path="references/keep.txt",
            media_type="text/plain",
            sha256=hashlib.sha256(b"Keep this.").hexdigest(),
            content_base64=base64.b64encode(b"Keep this.").decode("ascii"),
        ),
    ]
    entry = skills.create(original)
    version_one = skills.version(entry["id"], 1)
    run_id, owner = _resource_revision_run(store, entry["id"])
    revision = SkillResourceRevision(
        skill_id=entry["id"],
        expected_version=1,
        path="references/guide.md",
        content="Updated guide.",
    )

    result = skills.create_model_resource_revision_from_run(
        run_id, owner, "resource-revision-call", revision
    )

    version_two = skills.version(entry["id"], 2)
    assets = {item["path"]: item for item in version_two["assets"]}
    assert result["resource_edit"] == "replaced"
    assert result["resource_path"] == "references/guide.md"
    assert result["resource_sha256"] == hashlib.sha256(b"Updated guide.").hexdigest()
    assert version_two["content"] == version_one["content"]
    assert version_two["required_tools"] == version_one["required_tools"]
    assert version_two["required_permissions"] == version_one["required_permissions"]
    assert base64.b64decode(assets["references/guide.md"]["content_base64"]) == b"Updated guide."
    assert assets["references/guide.md"]["media_type"] == "text/markdown"
    assert base64.b64decode(assets["references/keep.txt"]["content_base64"]) == b"Keep this."
    assert version_two["evaluation_evidence"]["state"] == "candidate"
    assert version_two["evaluation_evidence"]["provenance"] == "model_resource_revision"
    assert version_two["evaluation_evidence"]["evaluations"] == []
    assert skills.get(entry["id"])["active_version"] is None
    assert skills.version(entry["id"], 1) == version_one
    with store.connection() as connection:
        call = connection.execute(
            "SELECT state FROM tool_calls WHERE run_id=? AND tool_name='procedure.resource_revise'",
            (run_id,),
        ).fetchone()
    assert call is not None and call["state"] == "completed"


def test_resource_revision_adds_one_utf8_file_and_refuses_path_escape(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-create.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    run_id, owner = _resource_revision_run(store, entry["id"])
    with pytest.raises(ValidationError, match="normalized relative POSIX"):
        SkillResourceRevision(
            skill_id=entry["id"], expected_version=1, path="../outside.txt", content="No"
        )
    revision = SkillResourceRevision(
        skill_id=entry["id"],
        expected_version=1,
        path="references/new.txt",
        content="Reference text.",
    )
    result = skills.create_model_resource_revision_from_run(run_id, owner, "new-resource", revision)
    version_two = skills.version(entry["id"], 2)
    assert result["resource_edit"] == "created"
    assert version_two["content"] == skills.version(entry["id"], 1)["content"]
    assert [asset["path"] for asset in version_two["assets"]] == ["references/new.txt"]


def test_model_resource_revision_writes_multiple_files_in_one_candidate_version(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-batch.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    run_id, owner = _resource_revision_run(store, entry["id"])
    revision = SkillResourceRevision(
        skill_id=entry["id"],
        expected_version=1,
        path="references/guide.md",
        content="Guide content.",
        additional_resources=[
            {"path": "templates/report.md", "content": "Template content."},
            {"path": "examples/result.txt", "content": "Example content."},
        ],
    )

    result = skills.create_model_resource_revision_from_run(
        run_id, owner, "resource-batch-call", revision
    )

    version_two = skills.version(entry["id"], 2)
    assert result["resource_edit_count"] == 3
    assert [item["path"] for item in result["resources"]] == [
        "references/guide.md",
        "templates/report.md",
        "examples/result.txt",
    ]
    assert {asset["path"] for asset in version_two["assets"]} == {
        "references/guide.md",
        "templates/report.md",
        "examples/result.txt",
    }
    assert version_two["evaluation_evidence"]["state"] == "candidate"
    assert version_two["evaluation_evidence"]["evaluations"] == []
    assert skills.get(entry["id"])["latest_version"] == 2
    assert skills.version(entry["id"], 1)["assets"] == []
    with pytest.raises(ValidationError, match="only once"):
        SkillResourceRevision(
            skill_id=entry["id"],
            expected_version=1,
            path="same.txt",
            content="First.",
            additional_resources=[{"path": "same.txt", "content": "Duplicate."}],
        )
    with pytest.raises(ValidationError, match="only once"):
        SkillResourceRevision(
            skill_id=entry["id"],
            expected_version=1,
            path="same.txt",
            content="Replace this.",
            remove_paths=["same.txt"],
        )
    with pytest.raises(ValidationError, match="at most eight"):
        SkillResourceRevision(
            skill_id=entry["id"],
            expected_version=1,
            path="write-0.txt",
            content="Write.",
            additional_resources=[
                {"path": f"write-{index}.txt", "content": "Write."} for index in range(1, 8)
            ],
            remove_paths=["remove.txt"],
        )
    with pytest.raises(ValidationError):
        SkillResourceRevision(
            skill_id=entry["id"],
            expected_version=1,
            path="write.txt",
            content="Write.",
            remove_paths=["remove-one.txt", "remove-two.txt"],
        )
    with pytest.raises(ValidationError):
        SkillResourceRevision(
            skill_id=entry["id"],
            expected_version=1,
            path="first.txt",
            content="First.",
            additional_resources=[
                {"path": f"extra-{index}.txt", "content": "More."} for index in range(8)
            ],
        )


def test_model_resource_batch_atomically_replaces_and_removes_with_both_grants(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-mixed-batch.sqlite3")
    skills = SkillStore(store)
    original = _draft()
    original.assets = [
        SkillAsset(
            path="references/replace.md",
            media_type="text/markdown",
            sha256=hashlib.sha256(b"old").hexdigest(),
            content_base64=base64.b64encode(b"old").decode("ascii"),
        ),
        SkillAsset(
            path="references/remove.md",
            media_type="text/markdown",
            sha256=hashlib.sha256(b"obsolete").hexdigest(),
            content_base64=base64.b64encode(b"obsolete").decode("ascii"),
        ),
    ]
    entry = skills.create(original)
    before = skills.version(entry["id"], 1)
    write_only_run, write_only_owner = _resource_revision_run(store, entry["id"])
    with pytest.raises(PermissionError, match="separate user grant"):
        skills.create_model_resource_revision_from_run(
            write_only_run,
            write_only_owner,
            "ungranted-removal",
            SkillResourceRevision(
                skill_id=entry["id"],
                expected_version=1,
                path="references/replace.md",
                content="must not write",
                remove_paths=["references/remove.md"],
            ),
        )
    assert skills.get(entry["id"])["latest_version"] == 1
    run_id, owner = _resource_revision_run(store, entry["id"], removal_authorized=True)
    missing = SkillResourceRevision(
        skill_id=entry["id"],
        expected_version=1,
        path="references/replace.md",
        content="must roll back",
        remove_paths=["references/missing.md"],
    )
    with pytest.raises(SkillConflict, match="no linked resource"):
        skills.create_model_resource_revision_from_run(run_id, owner, "missing-remove", missing)
    assert skills.get(entry["id"])["latest_version"] == 1
    assert skills.version(entry["id"], 1) == before

    revision = SkillResourceRevision(
        skill_id=entry["id"],
        expected_version=1,
        path="references/replace.md",
        content="updated",
        remove_paths=["references/remove.md"],
    )
    result = skills.create_model_resource_revision_from_run(run_id, owner, "mixed-edit", revision)

    after = skills.version(entry["id"], 2)
    assert result["resource_edit_count"] == 2
    assert result["resources"] == [
        {
            "path": "references/replace.md",
            "sha256": hashlib.sha256(b"updated").hexdigest(),
            "edit": "replaced",
        },
        {
            "path": "references/remove.md",
            "sha256": hashlib.sha256(b"obsolete").hexdigest(),
            "edit": "removed",
        },
    ]
    assert [asset["path"] for asset in after["assets"]] == ["references/replace.md"]
    assert base64.b64decode(after["assets"][0]["content_base64"]) == b"updated"
    assert after["content"] == before["content"]
    assert after["evaluation_evidence"]["provenance"] == "model_resource_batch_edit"
    assert skills.version(entry["id"], 1) == before
    assert skills.get(entry["id"])["active_version"] is None


def test_model_resource_removal_preserves_previous_version_and_other_package_fields(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-remove.sqlite3")
    skills = SkillStore(store)
    original = _draft()
    original.assets = [
        SkillAsset(
            path="references/remove.md",
            media_type="text/markdown",
            sha256=hashlib.sha256(b"remove me").hexdigest(),
            content_base64=base64.b64encode(b"remove me").decode("ascii"),
        ),
        SkillAsset(
            path="references/keep.txt",
            media_type="text/plain",
            sha256=hashlib.sha256(b"keep me").hexdigest(),
            content_base64=base64.b64encode(b"keep me").decode("ascii"),
        ),
    ]
    entry = skills.create(original)
    before = skills.version(entry["id"], 1)
    run_id = store.create_run(
        objective="Remove the obsolete reference.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": entry["id"], "version": 1},
        allow_procedure_resource_removal=True,
    )
    owner = store.acquire_lease(str(run_id["id"]), "procedure-resource-remove-test")
    assert owner is not None

    result = skills.create_model_resource_removal_from_run(
        str(run_id["id"]),
        owner,
        "remove-resource-call",
        SkillResourceRemoval(skill_id=entry["id"], expected_version=1, path="references/remove.md"),
    )

    after = skills.version(entry["id"], 2)
    assert result["removed_resource_path"] == "references/remove.md"
    assert after["content"] == before["content"]
    assert after["required_tools"] == before["required_tools"]
    assert [asset["path"] for asset in after["assets"]] == ["references/keep.txt"]
    assert base64.b64decode(after["assets"][0]["content_base64"]) == b"keep me"
    assert after["evaluation_evidence"]["state"] == "candidate"
    assert after["evaluation_evidence"]["evaluations"] == []
    assert skills.get(entry["id"])["active_version"] is None
    assert skills.version(entry["id"], 1) == before
    with store.connection() as connection:
        call = connection.execute(
            "SELECT state FROM tool_calls WHERE run_id=? AND tool_name='procedure.resource_remove'",
            (str(run_id["id"]),),
        ).fetchone()
    assert call is not None and call["state"] == "completed"


def test_resource_removal_rejects_missing_path_escape_and_missing_grant(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-remove-denied.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    removal_run = store.create_run(
        objective="Remove one selected resource.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": entry["id"], "version": 1},
        allow_procedure_resource_removal=True,
    )
    run_id = str(removal_run["id"])
    owner = store.acquire_lease(run_id, "procedure-resource-remove-test")
    assert owner is not None
    with pytest.raises(ValidationError, match="normalized relative POSIX"):
        SkillResourceRemoval(skill_id=entry["id"], expected_version=1, path="../outside.txt")
    with pytest.raises(SkillConflict, match="no linked resource"):
        skills.create_model_resource_removal_from_run(
            run_id,
            owner,
            "missing-resource-call",
            SkillResourceRemoval(skill_id=entry["id"], expected_version=1, path="missing.txt"),
        )
    denied_run, denied_owner = _run(store, entry["id"])
    with pytest.raises(PermissionError, match="no user authorization"):
        skills.create_model_resource_removal_from_run(
            denied_run,
            denied_owner,
            "denied-resource-remove",
            SkillResourceRemoval(skill_id=entry["id"], expected_version=1, path="missing.txt"),
        )


def test_resource_removal_requires_exclusive_run_grant(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-remove-combined.sqlite3")
    entry = SkillStore(store).create(_draft())
    with pytest.raises(ValueError, match="Choose only one"):
        store.create_run(
            objective="Conflicting edits.",
            acceptance_criteria=[],
            workspace=".",
            mode="observe",
            model="fixture",
            procedure_trial={"skill_id": entry["id"], "version": 1},
            allow_procedure_revision=True,
            allow_procedure_resource_removal=True,
        )


def test_resource_revision_requires_its_own_grant_and_rejects_combined_grants(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-grant.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    run_id, owner = _run(store, entry["id"])
    revision = SkillResourceRevision(
        skill_id=entry["id"], expected_version=1, path="notes.txt", content="No grant."
    )
    with pytest.raises(PermissionError, match="no user authorization"):
        skills.create_model_resource_revision_from_run(run_id, owner, "denied-resource", revision)
    with pytest.raises(ValueError, match="Choose only one"):
        store.create_run(
            objective="Conflicting revisions.",
            acceptance_criteria=[],
            workspace=".",
            mode="observe",
            model="fixture",
            procedure_trial={"skill_id": entry["id"], "version": 1},
            allow_procedure_revision=True,
            allow_procedure_resource_revision=True,
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


@pytest.mark.asyncio
async def test_coordinator_exposes_and_executes_separately_granted_resource_revision(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-coordinator.sqlite3")
    skills = SkillStore(store)
    entry = skills.create(_draft())
    run = store.create_run(
        objective="Add one linked reference file.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": entry["id"], "version": 1},
        allow_procedure_resource_revision=True,
    )
    revision = SkillResourceRevision(
        skill_id=entry["id"], expected_version=1, path="references/new.txt", content="Known text."
    )

    class ResourceRevisionProvider(LMStudioProvider):
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
            assert "procedure_resource_revise" in available
            assert "procedure_revise" not in available
            schema = available["procedure_resource_revise"]["function"]["parameters"]
            assert schema["properties"]["skill_id"]["enum"] == [entry["id"]]
            assert schema["properties"]["expected_version"]["enum"] == [1]
            call = {
                "id": "resource-revision-call",
                "type": "function",
                "function": {
                    "name": "procedure_resource_revise",
                    "arguments": json.dumps(revision.model_dump(mode="json")),
                },
            }
            if not any(message.get("role") == "tool" for message in messages):
                return {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "Resource candidate saved."}}
                ]
            }

    provider = ResourceRevisionProvider()
    try:
        await Coordinator(store, provider).execute(str(run["id"]))
    finally:
        await provider.close()
    saved = skills.version(entry["id"], 2)
    assert saved["content"] == skills.version(entry["id"], 1)["content"]
    assert base64.b64decode(saved["assets"][0]["content_base64"]) == b"Known text."
    assert saved["evaluation_evidence"]["state"] == "candidate"
    assert not store.unresolved_tool_calls(str(run["id"]))


@pytest.mark.asyncio
async def test_coordinator_exposes_and_executes_separately_granted_resource_removal(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-remove-coordinator.sqlite3")
    draft = _draft()
    draft.assets = [
        SkillAsset(
            path="references/remove.txt",
            media_type="text/plain",
            sha256=hashlib.sha256(b"obsolete").hexdigest(),
            content_base64=base64.b64encode(b"obsolete").decode("ascii"),
        )
    ]
    skills = SkillStore(store)
    entry = skills.create(draft)
    run = store.create_run(
        objective="Remove the obsolete reference.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": entry["id"], "version": 1},
        allow_procedure_resource_removal=True,
    )
    removal = SkillResourceRemoval(
        skill_id=entry["id"], expected_version=1, path="references/remove.txt"
    )

    class ResourceRemovalProvider(LMStudioProvider):
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
            assert "procedure_resource_remove" in available
            assert "procedure_resource_revise" not in available
            schema = available["procedure_resource_remove"]["function"]["parameters"]
            assert schema["properties"]["skill_id"]["enum"] == [entry["id"]]
            call = {
                "id": "resource-removal-call",
                "type": "function",
                "function": {
                    "name": "procedure_resource_remove",
                    "arguments": json.dumps(removal.model_dump(mode="json")),
                },
            }
            if not any(message.get("role") == "tool" for message in messages):
                return {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "Removal candidate saved."}}
                ]
            }

    provider = ResourceRemovalProvider()
    try:
        await Coordinator(store, provider).execute(str(run["id"]))
    finally:
        await provider.close()
    saved = skills.version(entry["id"], 2)
    assert saved["assets"] == []
    assert saved["evaluation_evidence"]["state"] == "candidate"
    assert not store.unresolved_tool_calls(str(run["id"]))


@pytest.mark.asyncio
async def test_coordinator_exposes_mixed_resource_edit_only_with_both_grants(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-mixed-coordinator.sqlite3")
    draft = _draft()
    draft.assets = [
        SkillAsset(
            path="references/replace.md",
            media_type="text/markdown",
            sha256=hashlib.sha256(b"old").hexdigest(),
            content_base64=base64.b64encode(b"old").decode("ascii"),
        ),
        SkillAsset(
            path="references/remove.md",
            media_type="text/markdown",
            sha256=hashlib.sha256(b"obsolete").hexdigest(),
            content_base64=base64.b64encode(b"obsolete").decode("ascii"),
        ),
    ]
    skills = SkillStore(store)
    entry = skills.create(draft)
    before = skills.version(entry["id"], 1)
    run = store.create_run(
        objective="Update and remove the selected linked references in one candidate.",
        acceptance_criteria=[],
        workspace=".",
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": entry["id"], "version": 1},
        allow_procedure_resource_revision=True,
        allow_procedure_resource_removal=True,
    )
    revision = SkillResourceRevision(
        skill_id=entry["id"],
        expected_version=1,
        path="references/replace.md",
        content="updated",
        remove_paths=["references/remove.md"],
    )

    class MixedResourceEditProvider(LMStudioProvider):
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
            assert "procedure_resource_revise" in available
            assert "procedure_resource_remove" not in available
            schema = available["procedure_resource_revise"]["function"]["parameters"]
            assert schema["properties"]["remove_paths"]
            call = {
                "id": "mixed-resource-edit-call",
                "type": "function",
                "function": {
                    "name": "procedure_resource_revise",
                    "arguments": json.dumps(revision.model_dump(mode="json")),
                },
            }
            if not any(message.get("role") == "tool" for message in messages):
                return {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}
            return {
                "choices": [{"message": {"role": "assistant", "content": "Mixed candidate saved."}}]
            }

    provider = MixedResourceEditProvider()
    try:
        await Coordinator(store, provider).execute(str(run["id"]))
    finally:
        await provider.close()

    saved = skills.version(entry["id"], 2)
    assert [asset["path"] for asset in saved["assets"]] == ["references/replace.md"]
    assert base64.b64decode(saved["assets"][0]["content_base64"]) == b"updated"
    assert skills.version(entry["id"], 1)["assets"] == before["assets"]
    assert saved["evaluation_evidence"]["provenance"] == "model_resource_batch_edit"
    assert not store.unresolved_tool_calls(str(run["id"]))


def test_api_persists_separate_procedure_resource_revision_grant(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-api.sqlite3")
    entry = SkillStore(store).create(_draft())
    app = create_app(LMStudioProvider(), SecretStr(API_TOKEN), store=store)
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            json={
                "objective": "Revise one linked reference.",
                "workspace": ".",
                "procedure_trial": {"skill_id": entry["id"], "version": 1},
                "allow_procedure_resource_revision": True,
            },
        )
    assert response.status_code == 201
    run = response.json()
    assert run["allow_procedure_revision"] is False
    assert run["allow_procedure_resource_revision"] is True
    grants = [
        event
        for event in store.list_events(run["id"])
        if event["event_type"] == "procedure.resource_revision_granted"
    ]
    assert len(grants) == 1
    assert grants[0]["payload"] == {
        "actor": "local_user",
        "skill_id": entry["id"],
        "expected_version": 1,
    }


def test_api_persists_separate_procedure_resource_removal_grant(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "procedure-resource-remove-api.sqlite3")
    entry = SkillStore(store).create(_draft())
    app = create_app(LMStudioProvider(), SecretStr(API_TOKEN), store=store)
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            json={
                "objective": "Remove one linked reference.",
                "workspace": ".",
                "procedure_trial": {"skill_id": entry["id"], "version": 1},
                "allow_procedure_resource_removal": True,
            },
        )
    assert response.status_code == 201
    run = response.json()
    assert run["allow_procedure_resource_revision"] is False
    assert run["allow_procedure_resource_removal"] is True
    grants = [
        event
        for event in store.list_events(run["id"])
        if event["event_type"] == "procedure.resource_removal_granted"
    ]
    assert len(grants) == 1
    assert grants[0]["payload"] == {
        "actor": "local_user",
        "skill_id": entry["id"],
        "expected_version": 1,
    }

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_skill_evaluations import draft, review, trial
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.restore import restore_database
from xaa.storage.runs import DATABASE_SCHEMA_VERSION, RunStore
from xaa.storage.skills import (
    SkillConflict,
    SkillLifecycleDecision,
    SkillPinDecision,
    SkillRevision,
    SkillStore,
)


def decision(summary, action, target=None):
    return SkillLifecycleDecision(
        action=action,
        target_version=target,
        expected_active_version=summary["active_version"],
        expected_latest_version=summary["latest_version"],
        expected_lifecycle_revision=summary["lifecycle_revision"],
        rationale="Explicit lifecycle test decision.",
    )


async def evaluated(store, skills, skill, version):
    first, second = await trial(store, skill, version), await trial(store, skill, version)
    skills.evaluate(skill["id"], version, review(first, second))


async def test_activate_upgrade_rollback_deprecate_and_snapshot_persistence(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "lifecycle.sqlite3")
    skills = SkillStore(store)
    skill = skills.create(draft())
    with pytest.raises(SkillConflict):
        skills.decide_lifecycle(skill["id"], decision(skill, "activate", 1))
    assert skills.lifecycle(skill["id"]) == []
    await evaluated(store, skills, skill, 1)
    active = skills.decide_lifecycle(skill["id"], decision(skills.get(skill["id"]), "activate", 1))
    assert active["active_version"] == 1 and active["lifecycle_revision"] == 1
    with pytest.raises(SkillConflict):
        skills.delete(skill["id"])
    skills.revise(
        skill["id"],
        SkillRevision(**draft("Version two procedure").model_dump(), expected_version=1),
    )
    assert skills.get(skill["id"])["active_version"] == 1
    await evaluated(store, skills, skill, 2)
    assert skills.get(skill["id"])["lifecycle_state"] == "active"
    stale_upgrade = decision(skills.get(skill["id"]), "activate", 2)
    newer = skills.decide_lifecycle(skill["id"], stale_upgrade)
    queued = store.create_run(
        objective="Use explicit active version",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": skill["id"], "version": 2, "purpose": "active_use"},
    )
    rolled = skills.decide_lifecycle(skill["id"], decision(newer, "rollback", 1))
    assert rolled["active_version"] == 1
    with pytest.raises(SkillConflict):
        skills.decide_lifecycle(skill["id"], stale_upgrade)
    with pytest.raises(ValueError):
        store.create_run(
            objective="Stale use",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            procedure_trial={"skill_id": skill["id"], "version": 2, "purpose": "active_use"},
        )
    deprecated = skills.decide_lifecycle(skill["id"], decision(rolled, "deprecate"))
    assert deprecated["active_version"] is None and deprecated["lifecycle_state"] == "deprecated"
    with pytest.raises(ValueError):
        store.create_run(
            objective="Deprecated trial",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            procedure_trial={"skill_id": skill["id"], "version": 1},
        )
    events = skills.lifecycle(skill["id"])
    assert [event["action"] for event in events] == [
        "activate",
        "activate",
        "rollback",
        "deprecate",
    ]
    restored = workspace_tmp_path / "restore" / "runs.sqlite3"
    restore_database(store.backup(), restored)
    assert SkillStore(RunStore(restored)).lifecycle(skill["id"]) == events
    skills.delete(skill["id"])
    assert store.get_run(queued["id"])["procedure_trial"]["purpose"] == "active_use"

    class Provider(LMStudioProvider):
        async def chat(self, *, model, messages, tools=None):
            if messages[-1]["role"] == "user":
                assert not any(
                    "Version two procedure" in (message.get("content") or "")
                    for message in messages
                )
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "lifecycle-view-1",
                                        "type": "function",
                                        "function": {
                                            "name": "procedure_view",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            assert "Version two procedure" in messages[-1]["content"]
            return {"choices": [{"message": {"role": "assistant", "content": "Snapshot used"}}]}

    provider = Provider()
    try:
        await Coordinator(store, provider).execute(queued["id"])
    finally:
        await provider.close()
    assert store.get_run(queued["id"])["state"] == "verifying"


async def test_lifecycle_api_is_authenticated_and_active_delete_is_conflict(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "api.sqlite3")
    skills = SkillStore(store)
    skill = skills.create(draft())
    await evaluated(store, skills, skill, 1)
    payload = decision(skills.get(skill["id"]), "activate", 1).model_dump()
    headers = {"Authorization": "Bearer " + "x" * 32}
    url = f"/skills/{skill['id']}/lifecycle"
    with TestClient(create_app(LMStudioProvider(), SecretStr("x" * 32), store=store)) as client:
        assert client.post(url, json=payload).status_code == 401
        response = client.post(url, headers=headers, json=payload)
        assert response.status_code == 200 and response.json()["active_version"] == 1
        assert client.post(url, headers=headers, json=payload).status_code == 409
        assert client.delete(f"/skills/{skill['id']}", headers=headers).status_code == 409
        events = client.get(url, headers=headers).json()
        assert len(events) == 1 and events[0]["actor"] == "local-user"
        selected = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Explicit active use",
                "procedure_trial": {"skill_id": skill["id"], "version": 1, "purpose": "active_use"},
            },
        )
        assert selected.status_code == 201
        assert selected.json()["procedure_trial"]["purpose"] == "active_use"

        pin_url = f"/skills/{skill['id']}/pin"
        assert client.put(pin_url, json={"pinned": True}).status_code == 401
        pinned = client.put(pin_url, headers=headers, json={"pinned": True})
        assert pinned.status_code == 200 and pinned.json()["pinned"] is True
        assert client.delete(f"/skills/{skill['id']}", headers=headers).status_code == 409
        unpinned = client.put(pin_url, headers=headers, json={"pinned": False})
        assert unpinned.status_code == 200 and unpinned.json()["pinned"] is False


def test_pinned_procedure_cannot_be_deleted_until_explicitly_unpinned(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "pin.sqlite3")
    skills = SkillStore(store)
    skill = skills.create(draft())
    pinned = skills.set_pinned(skill["id"], SkillPinDecision(pinned=True))
    assert pinned["pinned"] is True
    with pytest.raises(SkillConflict, match="Unpin"):
        skills.delete(skill["id"])
    unpinned = skills.set_pinned(skill["id"], SkillPinDecision(pinned=False))
    assert unpinned["pinned"] is False
    skills.delete(skill["id"])
    assert skill["id"] not in {row["id"] for row in skills.list()}


def test_pin_api_is_authenticated_and_reports_pin_in_skill_summary(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "pin-api.sqlite3")
    skill = SkillStore(store).create(draft())
    headers = {"Authorization": "Bearer " + "x" * 32}
    url = f"/skills/{skill['id']}/pin"
    with TestClient(create_app(LMStudioProvider(), SecretStr("x" * 32), store=store)) as client:
        assert client.put(url, json={"pinned": True}).status_code == 401
        response = client.put(url, headers=headers, json={"pinned": True})
        assert response.status_code == 200 and response.json()["pinned"] is True
        assert client.delete(f"/skills/{skill['id']}", headers=headers).status_code == 409
        response = client.put(url, headers=headers, json={"pinned": False})
        assert response.status_code == 200 and response.json()["pinned"] is False


def test_schema_three_migrates_additive_lifecycle_history(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "old.sqlite3")
    skill = SkillStore(store).create(draft())
    with store.connection() as connection:
        connection.execute("DROP TABLE skill_lifecycle_events")
        connection.execute("PRAGMA user_version=3")
    migrated = RunStore(store.path)
    assert SkillStore(migrated).get(skill["id"])["lifecycle_revision"] == 0
    assert SkillStore(migrated).lifecycle(skill["id"]) == []
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

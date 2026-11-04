import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.run_review import RunReviewDecision, review_run
from xaa.storage.restore import restore_database
from xaa.storage.runs import RunStore
from xaa.storage.skills import SkillConflict, SkillDraft, SkillEvaluation, SkillRevision, SkillStore


class Provider(LMStudioProvider):
    async def diagnose_tools(self, model, *, request=None):
        return True

    async def chat(self, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "Reviewed candidate"}}]}


def draft(content="Read evidence"):
    return SkillDraft(
        name="Evaluation fixture", content=content, required_tools=[], required_permissions=[]
    )


async def trial(store, skill, version=1, accept=True):
    run = store.create_run(
        objective="Trial fixture",
        acceptance_criteria=["Inspect candidate"],
        workspace=None,
        mode="observe",
        model="fixture",
        procedure_trial={"skill_id": skill["id"], "version": version},
    )
    provider = Provider()
    try:
        await Coordinator(store, provider).execute(run["id"])
    finally:
        await provider.close()
    if accept:
        saved = store.get_run(run["id"])
        await review_run(
            store,
            run["id"],
            RunReviewDecision(
                decision="accept",
                expected_revision=saved["revision"],
                rationale="Automated unit fixture confirms this synthetic candidate.",
                objective_confirmed=True,
                confirmed_criteria=[0],
            ),
        )
    return run["id"]


def review(first, second, count=0):
    return SkillEvaluation(
        expected_evaluation_count=count,
        trials=[
            {"run_id": first, "case_description": "First representative case"},
            {"run_id": second, "case_description": "Second representative case"},
        ],
        criteria=["Correct result", "Permission scope preserved"],
        confirmed_criteria=[0, 1],
        rationale="Explicit review of both synthetic trial outcomes.",
    )


async def test_evaluation_links_exact_completed_trials_and_preserves_history(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "eval.sqlite3")
    skills = SkillStore(store)
    skill = skills.create(draft())
    first, second = await trial(store, skill), await trial(store, skill)
    assert {row["id"] for row in skills.eligible_trials(skill["id"], 1)} == {first, second}
    evaluated = skills.evaluate(skill["id"], 1, review(first, second))
    assert evaluated["evaluation_evidence"]["state"] == "evaluated"
    entry = evaluated["evaluation_evidence"]["evaluations"][0]
    assert entry["actor"] == "local-user" and len(entry["trials"]) == 2
    assert all(item["final_review_sequence"] > 0 for item in entry["trials"])
    assert skills.get(skill["id"])["lifecycle_state"] == "evaluated"
    assert skills.get(skill["id"])["active_version"] is None
    with pytest.raises(SkillConflict):
        skills.evaluate(skill["id"], 1, review(first, second))
    skills.revise(
        skill["id"], SkillRevision(**draft("New candidate").model_dump(), expected_version=1)
    )
    assert skills.get(skill["id"])["lifecycle_state"] == "candidate"
    assert skills.version(skill["id"], 1)["evaluation_evidence"] == evaluated["evaluation_evidence"]
    assert skills.version(skill["id"], 2)["evaluation_evidence"]["state"] == "candidate"
    restored = workspace_tmp_path / "restored" / "runs.sqlite3"
    restore_database(store.backup(), restored)
    assert SkillStore(RunStore(restored)).version(skill["id"], 1) == evaluated


async def test_unaccepted_wrong_version_and_missing_trials_rejected(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "reject.sqlite3")
    skills = SkillStore(store)
    skill = skills.create(draft())
    first = await trial(store, skill)
    pending = await trial(store, skill, accept=False)
    with pytest.raises(SkillConflict):
        skills.evaluate(skill["id"], 1, review(first, pending))
    with pytest.raises(SkillConflict):
        skills.evaluate(skill["id"], 1, review(first, "missing"))
    skills.revise(
        skill["id"], SkillRevision(**draft("Version two").model_dump(), expected_version=1)
    )
    second = await trial(store, skill, version=2)
    with pytest.raises(SkillConflict):
        skills.evaluate(skill["id"], 1, review(first, second))
    assert skills.version(skill["id"], 1)["evaluation_evidence"]["state"] == "candidate"


@pytest.mark.parametrize(
    "change",
    [
        {"confirmed_criteria": [0]},
        {"confirmed_criteria": [0, 0]},
        {"rationale": " "},
        {"criteria": [" "]},
        {
            "trials": [
                {"run_id": "same", "case_description": "a"},
                {"run_id": "same", "case_description": "b"},
            ]
        },
        {
            "trials": [
                {"run_id": "a", "case_description": "Same"},
                {"run_id": "b", "case_description": " same "},
            ]
        },
    ],
)
def test_explicit_distinct_case_review_is_required(change):
    with pytest.raises(ValidationError):
        SkillEvaluation.model_validate({**review("a", "b").model_dump(), **change})


async def test_evaluation_api_requires_auth_and_leaves_procedure_inactive(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "api.sqlite3")
    skills = SkillStore(store)
    skill = skills.create(draft())
    first, second = await trial(store, skill), await trial(store, skill)
    headers = {"Authorization": "Bearer " + "x" * 32}
    url = f"/skills/{skill['id']}/versions/1/evaluations"
    with TestClient(create_app(LMStudioProvider(), SecretStr("x" * 32), store=store)) as client:
        assert client.post(url, json=review(first, second).model_dump()).status_code == 401
        assert (
            len(client.get(f"/skills/{skill['id']}/versions/1/trials", headers=headers).json()) == 2
        )
        response = client.post(url, headers=headers, json=review(first, second).model_dump())
        assert (
            response.status_code == 201
            and response.json()["evaluation_evidence"]["state"] == "evaluated"
        )
        assert (
            client.get(f"/skills/{skill['id']}", headers=headers).json()["active_version"] is None
        )
        assert (
            client.post(url, headers=headers, json=review(first, second).model_dump()).status_code
            == 409
        )

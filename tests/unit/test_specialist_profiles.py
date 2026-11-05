from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.tasks import TaskStore

TOKEN = "a-specialist-profile-test-token-long-enough"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class ProfileProvider(LMStudioProvider):
    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="parent/model"), ModelInfo(id="research/model")]

    async def close(self) -> None:
        return None


def test_specialist_route_is_persisted_and_snapshotted_into_saved_task(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-route.sqlite3")
    profiles = SpecialistProfileStore(store)
    assert {row["role"] for row in profiles.list()} == {
        "planner",
        "researcher",
        "developer",
        "reviewer",
        "tester",
        "executor",
    }
    profiles.set_route("researcher", "research/model")
    profiles.set_instructions("researcher", "Cite sources and label uncertainty.")
    run_id = store.create_run(
        objective="Delegate research",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="parent/model",
    )["id"]
    owner = store.acquire_lease(run_id, "fixture")
    TaskStore(store).save_plan(
        run_id,
        owner,
        [TaskDefinition(id="research", objective="Research", role="researcher")],
    )
    assert TaskStore(store).records(run_id)[0]["model_route"] == "research/model"
    assert TaskStore(store).records(run_id)[0]["identity_instructions"] == (
        "Cite sources and label uncertainty."
    )

    profiles.set_route("researcher", "parent/model")
    profiles.set_instructions("researcher", "Updated future-plan identity.")
    reopened = RunStore(store.path)
    assert SpecialistProfileStore(reopened).route_for("researcher", None) == "parent/model"
    assert TaskStore(reopened).records(run_id)[0]["model_route"] == "research/model"
    assert TaskStore(reopened).records(run_id)[0]["identity_instructions"] == (
        "Cite sources and label uncertainty."
    )


def test_specialist_profile_api_authenticates_and_only_accepts_discovered_models(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "specialist-profile-api.sqlite3")
    with TestClient(create_app(ProfileProvider(), SecretStr(TOKEN), store=store)) as client:
        assert client.get("/specialists/profiles").status_code == 401
        profiles = client.get("/specialists/profiles", headers=HEADERS)
        assert profiles.status_code == 200
        assert len(profiles.json()) == 6
        unknown = client.put(
            "/specialists/profiles/researcher/route",
            headers=HEADERS,
            json={"model_route": "missing/model"},
        )
        assert unknown.status_code == 409
        saved = client.put(
            "/specialists/profiles/researcher/route",
            headers=HEADERS,
            json={"model_route": "research/model"},
        )
        assert saved.status_code == 200
        assert saved.json()["model_route"] == "research/model"
        instructions = client.put(
            "/specialists/profiles/researcher/instructions",
            headers=HEADERS,
            json={"system_instructions": "Cite evidence and label uncertainty."},
        )
        assert instructions.status_code == 200
        assert instructions.json()["system_instructions"] == "Cite evidence and label uncertainty."
        assert (
            client.put(
                "/specialists/profiles/researcher/instructions",
                headers=HEADERS,
                json={"system_instructions": "x" * 4001},
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/specialists/profiles/unknown/route",
                headers=HEADERS,
                json={"model_route": None},
            ).status_code
            == 404
        )
        reset = client.put(
            "/specialists/profiles/researcher/route",
            headers=HEADERS,
            json={"model_route": None},
        )
        assert reset.status_code == 200
        assert reset.json()["model_route"] is None

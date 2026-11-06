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
    profiles.set_display_name("researcher", "Evidence Scout")
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
    assert TaskStore(store).records(run_id)[0]["identity_name"] == "Evidence Scout"
    assert TaskStore(store).records(run_id)[0]["identity_instructions"] == (
        "Cite sources and label uncertainty."
    )

    profiles.set_route("researcher", "parent/model")
    profiles.set_instructions("researcher", "Updated future-plan identity.")
    profiles.set_display_name("researcher", "Future Scout")
    reopened = RunStore(store.path)
    assert SpecialistProfileStore(reopened).route_for("researcher", None) == "parent/model"
    assert TaskStore(reopened).records(run_id)[0]["model_route"] == "research/model"
    assert TaskStore(reopened).records(run_id)[0]["identity_name"] == "Evidence Scout"
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
        identity = client.put(
            "/specialists/profiles/researcher/identity",
            headers=HEADERS,
            json={"display_name": "Evidence Scout"},
        )
        assert identity.status_code == 200
        assert identity.json()["display_name"] == "Evidence Scout"
        duplicate = client.put(
            "/specialists/profiles/developer/identity",
            headers=HEADERS,
            json={"display_name": "evidence scout"},
        )
        assert duplicate.status_code == 422
        assert (
            client.put(
                "/specialists/profiles/researcher/identity",
                headers=HEADERS,
                json={"display_name": " "},
            ).status_code
            == 422
        )
        assert client.get("/specialists/profiles/researcher/memory", headers=HEADERS).json() == []
        saved_memory = client.post(
            "/specialists/profiles/researcher/memory",
            headers=HEADERS,
            json={"content": "Prefer primary documentation."},
        )
        assert saved_memory.status_code == 201
        memory_id = saved_memory.json()["id"]
        assert (
            client.get("/specialists/profiles/researcher/memory", headers=HEADERS).json()[0][
                "content"
            ]
            == "Prefer primary documentation."
        )
        assert client.get("/specialists/profiles/developer/memory", headers=HEADERS).json() == []
        assert (
            client.delete(
                f"/specialists/profiles/researcher/memory/{memory_id}", headers=HEADERS
            ).status_code
            == 204
        )
        assert client.get("/specialists/profiles/researcher/memory", headers=HEADERS).json() == []
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

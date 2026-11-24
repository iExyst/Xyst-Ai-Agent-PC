import hashlib
import json
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.skills import snapshot_digest
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.tasks import TaskStore, validate_specialist_procedure_snapshot

TOKEN = "a-specialist-profile-test-token-long-enough"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _active_procedure(store: RunStore, *, identity: str = "procedure-a", version: int = 1) -> None:
    now = "2026-01-01T00:00:00+00:00"
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO skills(id,name,lifecycle_state,active_version,created_at,updated_at) VALUES(?,?,'active',?,?,?)",
            (identity, identity, version, now, now),
        )
        connection.execute(
            "INSERT INTO skill_versions(skill_id,version,content,required_tools,required_permissions,evaluation_evidence,created_at) VALUES(?,?,?,'[]','[]','{}',?)",
            (identity, version, f"Procedure body version {version}.", now),
        )


def _installed_python_environment(
    store: RunStore, identity: str = "procedure-a", version: int = 1
) -> tuple[str, str]:
    digest, source_digest, tree_digest = "a" * 64, "c" * 64, "b" * 64
    now = "2026-01-01T00:00:00+00:00"
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO skill_python_dependency_locks "
            "(skill_id,skill_version,environment_digest,source_sha256,lock_json,decision,created_at) "
            "VALUES(?,?,?,?,?,'approved',?)",
            (identity, version, digest, source_digest, "{}", now),
        )
        connection.execute(
            "INSERT INTO skill_python_dependency_environments "
            "(skill_id,skill_version,environment_digest,distribution,tree_sha256,installed_bytes,file_count,installed_at) "
            "VALUES(?,?,?,'Ubuntu-24.04',?,1,1,?)",
            (identity, version, digest, tree_digest, now),
        )
    return digest, tree_digest


def _add_resource(
    store: RunStore,
    identity: str,
    path: str,
    content: bytes,
    media_type: str = "text/markdown",
) -> None:
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO skill_version_assets(skill_id,version,relative_path,media_type,size_bytes,sha256,content) "
            "VALUES(?,1,?,?,?,?,?)",
            (
                identity,
                path,
                media_type,
                len(content),
                hashlib.sha256(content).hexdigest(),
                content,
            ),
        )


def _add_text_resource(store: RunStore, identity: str, path: str, content: bytes) -> None:
    _add_resource(store, identity, path, content)


def _make_text_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _make_blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_specialist_procedure_rejects_malformed_linked_png(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure-bad-image.sqlite")
    _active_procedure(store)
    _add_resource(store, "procedure-a", "references/diagram.png", b"not a PNG", "image/png")

    with pytest.raises(ValueError, match="file signature"):
        SpecialistProfileStore(store).set_procedure("researcher", "procedure-a")


def test_specialist_procedure_rejects_scanned_pdf_reference(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure-scanned-pdf.sqlite3")
    _active_procedure(store)
    _add_resource(
        store,
        "procedure-a",
        "references/scanned.pdf",
        _make_blank_pdf(),
        "application/pdf",
    )

    with pytest.raises(ValueError, match="scanned PDFs are not supported"):
        SpecialistProfileStore(store).set_procedure("researcher", "procedure-a")


def test_specialist_procedure_extracts_and_snapshots_bounded_text_pdf(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure-text-pdf.sqlite3")
    _active_procedure(store)
    pdf = _make_text_pdf("PDF procedure reference")
    _add_resource(store, "procedure-a", "references/guide.pdf", pdf, "application/pdf")
    SpecialistProfileStore(store).set_procedure("researcher", "procedure-a")

    run_id = store.create_run(
        objective="Read a PDF procedure reference",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="parent/model",
    )["id"]
    owner = store.acquire_lease(run_id, "fixture")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [TaskDefinition(id="read-pdf", objective="Read the PDF", role="researcher")],
    )
    snapshot = tasks.records(run_id)[0]["procedure_snapshot"]
    resource = snapshot["linked_resources"][0]
    assert resource["media_type"] == "application/pdf"
    assert resource["content"] == "[Page 1]\nPDF procedure reference"
    assert resource["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert (
        resource["extracted_text_sha256"]
        == hashlib.sha256(resource["content"].encode("utf-8")).hexdigest()
    )
    assert resource["source_size_bytes"] == str(len(pdf))
    validate_specialist_procedure_snapshot(snapshot)
    resource["content"] = "tampered extracted text"
    with pytest.raises(ValueError, match="snapshot integrity"):
        validate_specialist_procedure_snapshot(snapshot)


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


def test_specialist_procedure_is_selected_and_version_snapshotted(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure.sqlite3")
    _active_procedure(store)
    large_content = "x" * (20 * 1024)
    with store.connection() as connection:
        connection.execute(
            "UPDATE skill_versions SET content=? WHERE skill_id='procedure-a' AND version=1",
            (large_content,),
        )
    _add_text_resource(store, "procedure-a", "references/guide.md", b"G" * (50 * 1024))
    _add_text_resource(store, "procedure-a", "references/method.md", b"M" * (50 * 1024))
    profiles = SpecialistProfileStore(store)
    saved_profile = profiles.set_procedure("researcher", "procedure-a")
    assert saved_profile["procedure_skill_id"] == "procedure-a"
    assert saved_profile["procedure_name"] == "procedure-a"
    run_id = store.create_run(
        objective="Research with a saved procedure",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="parent/model",
    )["id"]
    owner = store.acquire_lease(run_id, "fixture")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id, owner, [TaskDefinition(id="research", objective="Research", role="researcher")]
    )
    saved = tasks.records(run_id)[0]["procedure_snapshot"]
    assert saved["content"] == large_content
    assert saved["version"] == 1
    assert saved["linked_resources"] == [
        {
            "path": "references/guide.md",
            "media_type": "text/markdown",
            "sha256": hashlib.sha256(b"G" * (50 * 1024)).hexdigest(),
            "content": "G" * (50 * 1024),
        },
        {
            "path": "references/method.md",
            "media_type": "text/markdown",
            "sha256": hashlib.sha256(b"M" * (50 * 1024)).hexdigest(),
            "content": "M" * (50 * 1024),
        },
    ]
    assert len(saved["snapshot_sha256"]) == 64
    validate_specialist_procedure_snapshot(saved)
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO skill_versions(skill_id,version,content,required_tools,required_permissions,evaluation_evidence,created_at) VALUES('procedure-a',2,'Procedure body version 2.','[]','[]','{}','2026-01-02T00:00:00+00:00')"
        )
        connection.execute("UPDATE skills SET active_version=2 WHERE id='procedure-a'")
    profiles.set_procedure("researcher", None)
    assert TaskStore(store).records(run_id)[0]["procedure_snapshot"] == saved


def test_specialist_credential_grant_is_explicit_and_bound_to_snapshot(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure-credentials.sqlite3")
    skill_id = str(uuid4())
    _active_procedure(store, identity=skill_id)
    declarations = json.dumps({"required_environment_names": ["API_TOKEN"]})
    with store.connection() as connection:
        connection.execute(
            "UPDATE skill_versions SET evaluation_evidence=? WHERE skill_id=? AND version=1",
            (json.dumps({"source": {"declared_requirements": declarations}}), skill_id),
        )
    SpecialistProfileStore(store).set_procedure("researcher", skill_id)
    run_id = store.create_run(
        objective="Research with a one-run credential grant",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="parent/model",
    )["id"]
    with store.connection() as connection:
        store.record_event(
            connection,
            run_id,
            "specialist.procedure_credentials_granted",
            {
                "grants": [
                    {
                        "role": "researcher",
                        "skill_id": skill_id,
                        "version": 1,
                        "credential_names": ["API_TOKEN"],
                    }
                ]
            },
        )
    owner = store.acquire_lease(run_id, "fixture")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id, owner, [TaskDefinition(id="research", objective="Research", role="researcher")]
    )
    snapshot = tasks.records(run_id)[0]["procedure_snapshot"]
    assert snapshot["credential_names"] == ["API_TOKEN"]
    assert "value" not in snapshot
    validate_specialist_procedure_snapshot(snapshot)

    ungranted_run_id = store.create_run(
        objective="Research without credentials",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="parent/model",
    )["id"]
    ungranted_owner = store.acquire_lease(ungranted_run_id, "fixture")
    TaskStore(store).save_plan(
        ungranted_run_id,
        ungranted_owner,
        [TaskDefinition(id="research", objective="Research", role="researcher")],
    )
    ungranted_snapshot = TaskStore(store).records(ungranted_run_id)[0]["procedure_snapshot"]
    assert "credential_names" not in ungranted_snapshot


def test_specialist_credential_api_rejects_wrong_procedure_and_undeclared_names(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure-credential-api.sqlite3")
    skill_id = str(uuid4())
    _active_procedure(store, identity=skill_id)
    declarations = json.dumps({"required_environment_names": ["API_TOKEN"]})
    with store.connection() as connection:
        connection.execute(
            "UPDATE skill_versions SET evaluation_evidence=? WHERE skill_id=? AND version=1",
            (json.dumps({"source": {"declared_requirements": declarations}}), skill_id),
        )

    class Credentials:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def get(self, profile: str = "lm-studio") -> str | None:
            return self.values.get(profile)

        def set(self, token: str, profile: str = "lm-studio") -> None:
            self.values[profile] = token

        def delete(self, profile: str = "lm-studio") -> None:
            self.values.pop(profile, None)

    credentials = Credentials()
    app = create_app(
        ProfileProvider(),
        SecretStr(TOKEN),
        store=store,
        credential_store=credentials,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        status_url = f"/skills/{skill_id}/versions/1/credentials"
        saved = client.put(
            f"{status_url}/API_TOKEN", headers=HEADERS, json={"value": "never-return-this"}
        )
        assert saved.status_code == 200
        selected = client.put(
            "/specialists/profiles/researcher/procedure",
            headers=HEADERS,
            json={"skill_id": skill_id},
        )
        assert selected.status_code == 200, selected.text
        grant_base = {
            "role": "researcher",
            "skill_id": skill_id,
            "version": 1,
            "credential_names": ["API_TOKEN"],
        }
        request_base = {
            "objective": "Test specialist procedure credential grant",
            "mode": "workspace",
            "workspace": str(workspace_tmp_path),
            "command_grant": {
                "backend": "wsl-bubblewrap-v1",
                "distribution": "Ubuntu-24.04",
                "profile_sha256": "a" * 64,
                "max_seconds": 30,
            },
        }
        wrong_version = client.post(
            "/runs",
            headers=HEADERS,
            json={
                **request_base,
                "specialist_credential_grants": [{**grant_base, "version": 2}],
            },
        )
        assert wrong_version.status_code == 409
        undeclared = client.post(
            "/runs",
            headers=HEADERS,
            json={
                **request_base,
                "specialist_credential_grants": [
                    {**grant_base, "credential_names": ["NOT_DECLARED"]}
                ],
            },
        )
        assert undeclared.status_code == 400


def test_specialist_procedure_python_environment_is_snapshotted_and_validated(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "specialist-procedure-environment.sqlite3")
    _active_procedure(store)
    digest, tree_digest = _installed_python_environment(store)
    with store.connection() as connection:
        connection.execute(
            "UPDATE skill_versions SET evaluation_evidence=? WHERE skill_id='procedure-a' AND version=1",
            (
                '{"source":{"declared_requirements":"{\\"python_dependency_specs\\":[\\"example>=1\\"]}"}}',
            ),
        )
    profile = SpecialistProfileStore(store).set_procedure(
        "executor", "procedure-a", python_environment_digest=digest
    )
    assert profile["procedure_python_environment_digest"] == digest
    run_id = store.create_run(
        objective="Use the selected procedure dependency environment",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="parent/model",
    )["id"]
    owner = store.acquire_lease(run_id, "fixture")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [TaskDefinition(id="execute", objective="Use the procedure", role="executor")],
    )
    snapshot = tasks.records(run_id)[0]["procedure_snapshot"]
    assert snapshot["required_dependency_managers"] == ["python"]
    assert snapshot["python_environment"] == {
        "environment_digest": digest,
        "distribution": "Ubuntu-24.04",
        "tree_sha256": tree_digest,
    }
    validate_specialist_procedure_snapshot(snapshot)
    snapshot["python_environment"]["tree_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="snapshot integrity"):
        validate_specialist_procedure_snapshot(snapshot)

    with pytest.raises(ValueError, match="approved installed python environment"):
        SpecialistProfileStore(store).set_procedure(
            "tester", "procedure-a", python_environment_digest="f" * 64
        )


def test_specialist_procedure_rejects_tampered_resource_snapshot(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure-integrity.sqlite3")
    _active_procedure(store)
    _add_text_resource(store, "procedure-a", "references/guide.md", b"Use primary sources.\n")
    SpecialistProfileStore(store).set_procedure("researcher", "procedure-a")
    run_id = store.create_run(
        objective="Research with a saved procedure",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="parent/model",
    )["id"]
    owner = store.acquire_lease(run_id, "fixture")
    TaskStore(store).save_plan(
        run_id, owner, [TaskDefinition(id="research", objective="Research", role="researcher")]
    )
    saved = TaskStore(store).records(run_id)[0]["procedure_snapshot"]
    saved["linked_resources"][0]["content"] = "Changed after snapshot."
    with pytest.raises(ValueError, match="snapshot integrity"):
        validate_specialist_procedure_snapshot(saved)


def test_specialist_task_snapshot_records_unavailable_procedure_permissions(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure-permissions.sqlite3")
    _active_procedure(store)
    with store.connection() as connection:
        connection.execute(
            "UPDATE skill_versions SET required_permissions=? WHERE skill_id='procedure-a' AND version=1",
            ('["workspace.read","workspace.write"]',),
        )
    SpecialistProfileStore(store).set_procedure("executor", "procedure-a")
    workspace = workspace_tmp_path / "project"
    workspace.mkdir()
    run_id = store.create_run(
        objective="Use assigned procedure",
        acceptance_criteria=[],
        workspace=str(workspace),
        mode="workspace",
        model="parent/model",
    )["id"]
    owner = store.acquire_lease(run_id, "fixture")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(id="read-only", objective="Read only", role="executor"),
            TaskDefinition(
                id="can-write",
                objective="Can write",
                role="executor",
                permissions=("workspace.read", "workspace.write"),
            ),
        ],
    )
    saved = {item["local_id"]: item["procedure_snapshot"] for item in tasks.records(run_id)}
    assert saved["read-only"]["unavailable_required_permissions"] == ["workspace.write"]
    assert "unavailable_required_permissions" not in saved["can-write"]
    validate_specialist_procedure_snapshot(saved["read-only"])
    validate_specialist_procedure_snapshot(saved["can-write"])


def test_legacy_resource_free_specialist_snapshot_remains_valid():
    snapshot = {
        "skill_id": "procedure-a",
        "version": 1,
        "name": "procedure-a",
        "content": "Legacy immutable specialist procedure.",
        "required_tools": [],
        "required_permissions": [],
    }
    snapshot["snapshot_sha256"] = snapshot_digest(snapshot)
    validate_specialist_procedure_snapshot(snapshot)


def test_specialist_procedure_api_lists_only_active_bounded_text_choices(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-procedure-api.sqlite3")
    _active_procedure(store)
    _add_text_resource(store, "procedure-a", "references/guide.md", b"Short guide.")
    with store.connection() as connection:
        connection.execute(
            "UPDATE skill_versions SET required_tools=?,required_permissions=? WHERE skill_id='procedure-a' AND version=1",
            ('[{"name":"workspace.search","version":"1"}]', '["workspace.read"]'),
        )
    _active_procedure(store, identity="large-procedure")
    _active_procedure(store, identity="expanded-procedure")
    _active_procedure(store, identity="binary-procedure")
    with store.connection() as connection:
        connection.execute(
            "UPDATE skill_versions SET content=? WHERE skill_id='large-procedure' AND version=1",
            ("x" * (80 * 1024 + 1),),
        )
        connection.execute(
            "UPDATE skill_versions SET content=? WHERE skill_id='expanded-procedure' AND version=1",
            ("x" * (20 * 1024),),
        )
    _add_text_resource(store, "large-procedure", "references/large.md", b"x")
    _add_resource(
        store, "binary-procedure", "references/guide.pdf", b"%PDF-1.7\n", "application/pdf"
    )
    _active_procedure(store, identity="inactive-procedure")
    with store.connection() as connection:
        connection.execute(
            "UPDATE skills SET lifecycle_state='candidate' WHERE id='inactive-procedure'"
        )
    with TestClient(create_app(ProfileProvider(), SecretStr(TOKEN), store=store)) as client:
        choices = client.get("/specialists/procedures", headers=HEADERS)
        assert choices.status_code == 200
        assert choices.json() == [
            {
                "id": "expanded-procedure",
                "name": "expanded-procedure",
                "version": 1,
                "linked_resource_count": 0,
                "required_tools": [],
                "required_permissions": [],
                "python_environments": [],
                "npm_environments": [],
            },
            {
                "id": "procedure-a",
                "name": "procedure-a",
                "version": 1,
                "linked_resource_count": 1,
                "required_tools": [{"name": "workspace.search", "version": "1"}],
                "required_permissions": ["workspace.read"],
                "python_environments": [],
                "npm_environments": [],
            },
        ]
        selected = client.put(
            "/specialists/profiles/researcher/procedure",
            headers=HEADERS,
            json={"skill_id": "procedure-a"},
        )
        assert selected.status_code == 200
        assert selected.json()["procedure_skill_id"] == "procedure-a"
        expanded = client.put(
            "/specialists/profiles/tester/procedure",
            headers=HEADERS,
            json={"skill_id": "expanded-procedure"},
        )
        assert expanded.status_code == 200
        assert expanded.json()["procedure_skill_id"] == "expanded-procedure"
        for unsupported in ("large-procedure", "binary-procedure"):
            rejected_resource = client.put(
                "/specialists/profiles/tester/procedure",
                headers=HEADERS,
                json={"skill_id": unsupported},
            )
            assert rejected_resource.status_code == 422
        rejected = client.put(
            "/specialists/profiles/researcher/procedure",
            headers=HEADERS,
            json={"skill_id": "inactive-procedure"},
        )
        assert rejected.status_code == 422
        researcher = next(
            item
            for item in client.get("/specialists/profiles", headers=HEADERS).json()
            if item["role"] == "researcher"
        )
        assert researcher["procedure_skill_id"] == "procedure-a"


def test_specialist_procedure_api_lists_and_binds_approved_installed_environments(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "specialist-procedure-environments-api.sqlite3")
    _active_procedure(store)
    digest, tree_digest = _installed_python_environment(store)
    with TestClient(create_app(ProfileProvider(), SecretStr(TOKEN), store=store)) as client:
        choices = client.get("/specialists/procedures", headers=HEADERS)
        assert choices.status_code == 200
        procedure = choices.json()[0]
        assert procedure["python_environments"] == [
            {
                "environment_digest": digest,
                "distribution": "Ubuntu-24.04",
                "tree_sha256": tree_digest,
                "installed_bytes": 1,
                "file_count": 1,
                "node_version": None,
                "npm_version": None,
            }
        ]
        assert procedure["npm_environments"] == []
        saved = client.put(
            "/specialists/profiles/executor/procedure",
            headers=HEADERS,
            json={"skill_id": "procedure-a", "python_environment_digest": digest},
        )
        assert saved.status_code == 200
        assert saved.json()["procedure_python_environment_digest"] == digest
        invalid = client.put(
            "/specialists/profiles/tester/procedure",
            headers=HEADERS,
            json={"skill_id": "procedure-a", "python_environment_digest": "f" * 64},
        )
        assert invalid.status_code == 422


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

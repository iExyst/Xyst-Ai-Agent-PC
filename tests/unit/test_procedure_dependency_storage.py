import base64
import hashlib
import io
import json
import uuid
import zipfile

import pytest
from xaa.extensions.procedure_dependencies import (
    LockedPythonWheel,
    ProcedurePythonDependencyLock,
    PythonDependencyTarget,
)
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import DATABASE_SCHEMA_VERSION, RunState, RunStore
from xaa.storage.skills import SkillAsset, SkillConflict, SkillDraft, SkillStore, SkillTrial

SOURCE_SHA256 = "b" * 64


def idna_wheel() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "idna-3.10.dist-info/METADATA",
            "Metadata-Version: 2.3\nName: idna\nVersion: 3.10\n\n",
        )
        archive.writestr(
            "idna-3.10.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
        )
        archive.writestr("idna/__init__.py", "")
    return output.getvalue()


def imported_skill(skills: SkillStore, *, python_script: bool = False) -> dict:
    script = b"import idna\nprint(idna.__version__)\n"
    return skills.import_agent_plugin(
        SkillDraft(
            name="Imported test procedure",
            content="A test procedure.",
            required_tools=[],
            required_permissions=[],
            assets=(
                [
                    SkillAsset(
                        path="scripts/check.py",
                        content_base64=base64.b64encode(script).decode("ascii"),
                        media_type="text/x-python",
                        sha256=hashlib.sha256(script).hexdigest(),
                    )
                ]
                if python_script
                else []
            ),
        ),
        {
            "content_sha256": SOURCE_SHA256,
            "declared_requirements": json.dumps({"python_dependency_specs": ["idna>=3,<4"]}),
        },
        source_kind="hermes_skill",
    )


def lock_for(
    skill: dict,
    *,
    source_sha256: str = SOURCE_SHA256,
    requirements: list[str] | None = None,
    platform_version: str = "#1 SMP",
):
    content = idna_wheel()
    return ProcedurePythonDependencyLock(
        schema_version=1,
        skill_id=skill["id"],
        skill_version=1,
        source_sha256=source_sha256,
        target=PythonDependencyTarget(
            distribution="Ubuntu-24.04",
            environment={
                "implementation_name": "cpython",
                "implementation_version": "3.12.3",
                "os_name": "posix",
                "platform_machine": "x86_64",
                "platform_python_implementation": "CPython",
                "platform_release": "6.8.0-wsl2",
                "platform_system": "Linux",
                "platform_version": platform_version,
                "python_full_version": "3.12.3",
                "python_version": "3.12",
                "sys_platform": "linux",
            },
            compatible_wheel_tags=["py3-none-any"],
        ),
        requirements=["idna>=3,<4"] if requirements is None else requirements,
        wheels=[
            LockedPythonWheel(
                filename="idna-3.10-py3-none-any.whl",
                project_name="idna",
                version="3.10",
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
        ],
    )


def test_lock_persistence_is_version_bound_idempotent_and_owner_reviewed(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "dependency-locks.sqlite3")
    skills = SkillStore(runs)
    skill = imported_skill(skills)
    lock = lock_for(skill)

    saved = skills.save_python_dependency_lock(lock)
    assert saved["decision"] == "pending_review"
    assert saved["environment_digest"] == lock.environment_digest()
    assert skills.save_python_dependency_lock(lock) == saved
    assert skills.python_dependency_locks(skill["id"], 1) == [saved]

    with pytest.raises(SkillConflict, match="Acquire and verify"):
        skills.review_python_dependency_lock(skill["id"], 1, lock.environment_digest(), "approved")
    verified = skills.store_python_dependency_wheelhouse(
        lock, {lock.wheels[0].filename: idna_wheel()}
    )
    assert verified[lock.wheels[0].filename] == idna_wheel()

    approved = skills.review_python_dependency_lock(
        skill["id"], 1, lock.environment_digest(), "approved"
    )
    assert approved["decision"] == "approved"
    assert approved["reviewed_at"]
    assert (
        skills.review_python_dependency_lock(skill["id"], 1, lock.environment_digest(), "approved")
        == approved
    )
    with pytest.raises(SkillConflict, match="final owner decision"):
        skills.review_python_dependency_lock(skill["id"], 1, lock.environment_digest(), "rejected")

    with runs.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
    assert ProcedurePythonDependencyLock.model_validate(saved["lock"]) == lock


def test_lock_rejects_wrong_source_and_non_imported_procedure(workspace_tmp_path):
    skills = SkillStore(RunStore(workspace_tmp_path / "dependency-lock-source.sqlite3"))
    imported = imported_skill(skills)
    with pytest.raises(SkillConflict, match="source digest"):
        skills.save_python_dependency_lock(lock_for(imported, source_sha256="c" * 64))

    authored = skills.create(
        SkillDraft(
            name="User procedure",
            content="A user procedure.",
            required_tools=[],
            required_permissions=[],
        )
    )
    with pytest.raises(SkillConflict, match="source digest"):
        skills.save_python_dependency_lock(lock_for(authored))


def test_lock_requirements_must_match_the_imported_declarations(workspace_tmp_path):
    skills = SkillStore(RunStore(workspace_tmp_path / "dependency-lock-requirements.sqlite3"))
    skill = imported_skill(skills)
    with pytest.raises(SkillConflict, match="do not match imported Python declarations"):
        skills.save_python_dependency_lock(lock_for(skill, requirements=["idna>=3,<5"]))


def test_run_selects_only_the_approved_installed_environment_for_its_wsl_grant(
    workspace_tmp_path,
):
    runs = RunStore(workspace_tmp_path / "dependency-environment-run.sqlite3")
    skills = SkillStore(runs)
    skill = imported_skill(skills, python_script=True)
    lock = lock_for(skill)
    skills.save_python_dependency_lock(lock)
    skills.store_python_dependency_wheelhouse(lock, {lock.wheels[0].filename: idna_wheel()})
    skills.review_python_dependency_lock(skill["id"], 1, lock.environment_digest(), "approved")
    skills.record_python_dependency_environment(
        lock,
        {
            "environment_digest": lock.environment_digest(),
            "lock_sha256": hashlib.sha256(lock.canonical_json().encode("ascii")).hexdigest(),
            "installed": True,
            "tree_sha256": "c" * 64,
            "installed_bytes": 128,
            "file_count": 2,
        },
    )
    project = workspace_tmp_path.parent / f"project-{uuid.uuid4().hex}"
    project.mkdir()
    run = runs.create_run(
        objective="Run the selected procedure",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        procedure_trial=SkillTrial(
            skill_id=skill["id"], version=1, purpose="evaluation_trial"
        ).model_dump(),
        command_grant={
            "backend": "wsl-bubblewrap-v1",
            "distribution": "Ubuntu-24.04",
            "profile_sha256": "a" * 64,
            "max_seconds": 10,
        },
    )
    assert run["procedure_trial"]["python_environment"] == {
        "environment_digest": lock.environment_digest(),
        "distribution": "Ubuntu-24.04",
        "tree_sha256": "c" * 64,
    }
    owner = runs.acquire_lease(run["id"], "fixture")
    runs.transition(run["id"], RunState.PLANNING, owner=owner)
    runs.transition(run["id"], RunState.RUNNING, owner=owner)
    intent = CommandJournal(runs).reserve(
        run["id"],
        owner,
        uuid.uuid4().hex,
        {"argv": ["python", "scripts/check.py"]},
        idempotency_key=uuid.uuid4().hex,
    )
    assert intent.procedure_environment_digest == lock.environment_digest()
    assert intent.procedure_environment_tree_sha256 == "c" * 64

    second_lock = lock_for(skill, platform_version="#2 SMP")
    skills.save_python_dependency_lock(second_lock)
    skills.store_python_dependency_wheelhouse(
        second_lock, {second_lock.wheels[0].filename: idna_wheel()}
    )
    skills.review_python_dependency_lock(
        skill["id"], 1, second_lock.environment_digest(), "approved"
    )
    skills.record_python_dependency_environment(
        second_lock,
        {
            "environment_digest": second_lock.environment_digest(),
            "lock_sha256": hashlib.sha256(second_lock.canonical_json().encode("ascii")).hexdigest(),
            "installed": True,
            "tree_sha256": "d" * 64,
            "installed_bytes": 128,
            "file_count": 2,
        },
    )
    run_arguments = {
        "objective": "Choose an installed procedure environment",
        "acceptance_criteria": [],
        "workspace": str(project),
        "mode": "workspace",
        "model": None,
        "procedure_trial": SkillTrial(
            skill_id=skill["id"], version=1, purpose="evaluation_trial"
        ).model_dump(),
        "command_grant": {
            "backend": "wsl-bubblewrap-v1",
            "distribution": "Ubuntu-24.04",
            "profile_sha256": "a" * 64,
            "max_seconds": 10,
        },
    }
    with pytest.raises(ValueError, match="Choose an approved installed Python environment"):
        runs.create_run(**run_arguments)
    chosen = runs.create_run(
        **run_arguments,
        procedure_python_environment_digest=second_lock.environment_digest(),
    )
    assert chosen["procedure_trial"]["python_environment"] == {
        "environment_digest": second_lock.environment_digest(),
        "distribution": "Ubuntu-24.04",
        "tree_sha256": "d" * 64,
    }
    with pytest.raises(ValueError, match="Choose an approved installed Python environment"):
        runs.create_run(**run_arguments, procedure_python_environment_digest="f" * 64)

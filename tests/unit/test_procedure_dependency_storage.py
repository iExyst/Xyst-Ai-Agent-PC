import base64
import hashlib
import io
import json
import tarfile
import uuid
import zipfile

import pytest
from xaa.extensions.npm_dependencies import (
    NpmLockedPackage,
    NpmRootDependency,
    ProcedureNpmDependencyLock,
)
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
    chosen_owner = runs.acquire_lease(chosen["id"], "fixture-selected-environment")
    runs.transition(chosen["id"], RunState.PLANNING, owner=chosen_owner)
    runs.transition(chosen["id"], RunState.RUNNING, owner=chosen_owner)
    chosen_intent = CommandJournal(runs).reserve(
        chosen["id"],
        chosen_owner,
        uuid.uuid4().hex,
        {"argv": ["python", "scripts/check.py"]},
        idempotency_key=uuid.uuid4().hex,
    )
    assert chosen_intent.procedure_environment_digest == second_lock.environment_digest()
    assert chosen_intent.procedure_environment_tree_sha256 == "d" * 64
    with pytest.raises(ValueError, match="Choose an approved installed Python environment"):
        runs.create_run(**run_arguments, procedure_python_environment_digest="f" * 64)


def test_run_binds_approved_npm_tree_to_exact_node_procedure_and_command(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "npm-environment-run.sqlite3")
    skills = SkillStore(runs)
    package = {"name": "xaa-offline-fixture", "version": "1.0.0", "main": "index.js"}
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        for name, contents in (
            ("package/package.json", json.dumps(package).encode()),
            ("package/index.js", b"module.exports = 'ready';\\n"),
        ):
            entry = tarfile.TarInfo(name)
            entry.size = len(contents)
            archive.addfile(entry, io.BytesIO(contents))
    tarball = archive_buffer.getvalue()
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode("ascii")
    resolved = "https://registry.npmjs.org/xaa-offline-fixture/-/xaa-offline-fixture-1.0.0.tgz"
    manifest = b'{"name":"fixture-project","dependencies":{"xaa-offline-fixture":"^1.0.0"}}'
    package_lock = json.dumps(
        {
            "name": "fixture-project",
            "lockfileVersion": 3,
            "packages": {
                "": {
                    "name": "fixture-project",
                    "dependencies": {"xaa-offline-fixture": "^1.0.0"},
                },
                "node_modules/xaa-offline-fixture": {
                    "version": "1.0.0",
                    "resolved": resolved,
                    "integrity": integrity,
                },
            },
        },
        separators=(",", ":"),
    ).encode()
    script = b"console.log(require('xaa-offline-fixture'));\\n"

    def asset(path: str, content: bytes, media_type: str = "application/json") -> SkillAsset:
        return SkillAsset(
            path=path,
            content_base64=base64.b64encode(content).decode("ascii"),
            media_type=media_type,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    skill = skills.import_agent_plugin(
        SkillDraft(
            name="Imported Node procedure",
            content="Run the linked Node script.",
            required_tools=[],
            required_permissions=[],
            assets=[
                asset("package.json", manifest),
                asset("package-lock.json", package_lock),
                asset("scripts/run.cjs", script, "application/javascript"),
            ],
        ),
        {
            "content_sha256": SOURCE_SHA256,
            "declared_requirements": json.dumps(
                {"npm_dependency_specs": ["xaa-offline-fixture@^1.0.0"]}
            ),
        },
        source_kind="hermes_skill",
    )

    def create_lock(node_version: str, npm_version: str) -> ProcedureNpmDependencyLock:
        return ProcedureNpmDependencyLock(
            schema_version=1,
            skill_id=skill["id"],
            skill_version=1,
            source_sha256=SOURCE_SHA256,
            package_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            lockfile_sha256=hashlib.sha256(package_lock).hexdigest(),
            distribution="Ubuntu-24.04",
            node_version=node_version,
            npm_version=npm_version,
            requirements=("xaa-offline-fixture@^1.0.0",),
            root_dependencies=(
                NpmRootDependency(
                    name="xaa-offline-fixture", version_range="^1.0.0", kind="dependencies"
                ),
            ),
            packages=(
                NpmLockedPackage(
                    package_path="node_modules/xaa-offline-fixture",
                    name="xaa-offline-fixture",
                    version="1.0.0",
                    resolved_url=resolved,
                    integrity=integrity,
                ),
            ),
        )

    def install_fixture(lock: ProcedureNpmDependencyLock, tree: str) -> None:
        skills.save_npm_dependency_lock(lock)
        archive_path = workspace_tmp_path / f"{lock.environment_digest()}.tgz"
        archive_path.write_bytes(tarball)
        skills.store_npm_dependency_tarballs(
            lock, {"node_modules/xaa-offline-fixture": archive_path}
        )
        skills.review_npm_dependency_lock(
            skill["id"], 1, lock.environment_digest(), "approved"
        )
        skills.record_npm_dependency_environment(
            lock,
            {
                "environment_digest": lock.environment_digest(),
                "lock_sha256": lock.environment_digest(),
                "installed": True,
                "distribution": lock.distribution,
                "node_version": lock.node_version,
                "npm_version": lock.npm_version,
                "tree_sha256": tree,
                "installed_bytes": 128,
                "file_count": 2,
            },
        )

    first = create_lock("v22.2.0", "10.8.1")
    install_fixture(first, "c" * 64)
    project = workspace_tmp_path.parent / f"node-project-{uuid.uuid4().hex}"
    project.mkdir()
    run_arguments = {
        "objective": "Run the selected Node procedure",
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
    automatic = runs.create_run(**run_arguments)
    assert automatic["procedure_trial"]["npm_environment"] == {
        "environment_digest": first.environment_digest(),
        "distribution": "Ubuntu-24.04",
        "tree_sha256": "c" * 64,
        "node_version": "v22.2.0",
        "npm_version": "10.8.1",
    }

    second = create_lock("v22.2.0", "10.8.2")
    install_fixture(second, "d" * 64)
    with pytest.raises(ValueError, match="Choose an approved installed npm environment"):
        runs.create_run(**run_arguments)
    chosen = runs.create_run(
        **run_arguments,
        procedure_npm_environment_digest=second.environment_digest(),
    )
    assert chosen["procedure_trial"]["npm_environment"]["tree_sha256"] == "d" * 64
    owner = runs.acquire_lease(chosen["id"], "selected-node-procedure")
    runs.transition(chosen["id"], RunState.PLANNING, owner=owner)
    runs.transition(chosen["id"], RunState.RUNNING, owner=owner)
    intent = CommandJournal(runs).reserve(
        chosen["id"],
        owner,
        uuid.uuid4().hex,
        {"argv": ["node", "scripts/run.cjs"]},
        idempotency_key=uuid.uuid4().hex,
    )
    assert intent.procedure_node_environment_digest == second.environment_digest()
    assert intent.procedure_node_environment_tree_sha256 == "d" * 64
    assert intent.procedure_skill_id == skill["id"]
    assert intent.procedure_version == 1
    with pytest.raises(ValueError, match="approved installed npm environment"):
        runs.create_run(
            **run_arguments,
            procedure_npm_environment_digest="f" * 64,
        )

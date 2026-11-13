import pytest
from pydantic import ValidationError
from xaa.extensions.procedure_dependencies import (
    LockedPythonWheel,
    ProcedurePythonDependencyLock,
    PythonDependencyTarget,
)


def target(
    *, system: str = "Linux", tags: tuple[str, ...] = ("py3-none-any",)
) -> PythonDependencyTarget:
    return PythonDependencyTarget(
        distribution="Ubuntu-24.04",
        compatible_wheel_tags=list(tags),
        environment={
            "implementation_name": "cpython",
            "implementation_version": "3.12.3",
            "os_name": "posix",
            "platform_machine": "x86_64",
            "platform_python_implementation": "CPython",
            "platform_release": "6.8.0-microsoft-standard-WSL2",
            "platform_system": system,
            "platform_version": "#1 SMP",
            "python_full_version": "3.12.3",
            "python_version": "3.12",
            "sys_platform": "linux" if system == "Linux" else "win32",
        },
    )


def wheel(*, version: str = "3.10", filename: str | None = None) -> LockedPythonWheel:
    return LockedPythonWheel(
        filename=filename or f"idna-{version}-py3-none-any.whl",
        project_name="idna",
        version=version,
        sha256="a" * 64,
        size_bytes=12_345,
    )


def lock(
    *,
    requirements: list[str] | None = None,
    wheels: list[LockedPythonWheel] | None = None,
    target_value: PythonDependencyTarget | None = None,
):
    return ProcedurePythonDependencyLock(
        schema_version=1,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=2,
        source_sha256="b" * 64,
        target=target() if target_value is None else target_value,
        requirements=["idna>=3,<4"] if requirements is None else requirements,
        wheels=[wheel()] if wheels is None else wheels,
    )


def test_dependency_lock_hash_is_canonical_and_binds_target_and_artifacts():
    first = lock(
        requirements=["idna>=3,<4", "charset-normalizer>=3"],
        wheels=[
            wheel(),
            LockedPythonWheel(
                filename="charset_normalizer-3.4.2-py3-none-any.whl",
                project_name="charset-normalizer",
                version="3.4.2",
                sha256="c" * 64,
                size_bytes=23_456,
            ),
        ],
    )
    second = lock(
        requirements=["charset-normalizer>=3", "idna>=3,<4"],
        wheels=list(reversed(first.wheels)),
    )

    assert first.environment_digest() == second.environment_digest()
    assert first.environment_digest() != lock(wheels=[wheel(version="3.9")]).environment_digest()


def test_dependency_lock_json_round_trip_preserves_immutable_identity():
    original = lock()
    restored = ProcedurePythonDependencyLock.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.environment_digest() == original.environment_digest()


def test_requirement_marker_is_checked_against_target_environment():
    linux_lock = lock(requirements=["idna>=3; sys_platform == 'linux'"])
    windows = target(system="Windows")

    windows_lock = ProcedurePythonDependencyLock(
        schema_version=1,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=2,
        source_sha256="b" * 64,
        target=windows,
        requirements=["idna>=3; sys_platform == 'linux'"],
        wheels=[],
    )

    assert linux_lock.requirements == ('idna>=3; sys_platform == "linux"',)
    assert windows_lock.wheels == ()


@pytest.mark.parametrize(
    ("requirements", "wheels"),
    [
        (["idna @ https://example.invalid/idna.whl"], [wheel()]),
        (["idna>=3"], [wheel(version="2.9")]),
    ],
)
def test_lock_rejects_direct_urls_and_unsatisfied_versions(requirements, wheels):
    with pytest.raises(ValidationError):
        lock(requirements=requirements, wheels=wheels)


def test_wheel_rejects_path_filename():
    with pytest.raises(ValidationError, match="basename"):
        wheel(filename="../idna-3.10-py3-none-any.whl")


def test_lock_rejects_non_pypi_index_and_duplicate_wheel_projects():
    with pytest.raises(ValidationError):
        ProcedurePythonDependencyLock(
            schema_version=1,
            skill_id="12345678-1234-4234-8234-123456789abc",
            skill_version=2,
            source_sha256="b" * 64,
            target=target(),
            requirements=["idna>=3"],
            wheels=[wheel()],
            index_url="https://example.invalid/simple",
        )

    with pytest.raises(ValidationError, match="one wheel per project"):
        lock(wheels=[wheel(), wheel()])


def test_lock_rejects_noncanonical_skill_id():
    with pytest.raises(ValidationError, match="canonical UUID"):
        ProcedurePythonDependencyLock(
            schema_version=1,
            skill_id="not-a-uuid-0000-000000000000",
            skill_version=2,
            source_sha256="b" * 64,
            target=target(),
            requirements=["idna>=3"],
            wheels=[wheel()],
        )


def test_lock_rejects_wheel_incompatible_with_target_abi():
    with pytest.raises(ValidationError, match="incompatible with the target Python ABI"):
        lock(
            wheels=[wheel(filename="idna-3.10-cp311-cp311-manylinux_2_28_x86_64.whl")],
            target_value=target(tags=("cp312-cp312-manylinux_2_28_x86_64",)),
        )


def test_lock_copies_inputs_into_immutable_collections():
    requirements = ["idna>=3"]
    wheels = [wheel()]
    environment = target().environment
    locked = lock(requirements=requirements, wheels=wheels)

    requirements.append("other>=1")
    wheels.clear()

    assert len(locked.requirements) == 1
    assert len(locked.wheels) == 1
    assert isinstance(locked.target.environment, tuple)
    assert isinstance(environment, tuple)

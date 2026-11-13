import json
from collections.abc import Mapping

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


def wheel(
    *, version: str = "3.10", filename: str | None = None, requires_dist: list[str] | None = None
) -> LockedPythonWheel:
    return LockedPythonWheel(
        filename=filename or f"idna-{version}-py3-none-any.whl",
        project_name="idna",
        version=version,
        sha256="a" * 64,
        size_bytes=12_345,
        requires_dist=[] if requires_dist is None else requires_dist,
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


def test_empty_requires_dist_preserves_pre_metadata_lock_digest():
    original = lock()
    legacy_payload = original.model_dump(mode="json")
    for wheel_data in legacy_payload["wheels"]:
        wheel_data.pop("requires_dist")
    legacy_canonical = json.dumps(
        legacy_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )

    assert original.canonical_json() == legacy_canonical


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
        (["idna[fast]>=3"], [wheel()]),
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


def test_lock_requires_transitive_wheels_from_normalized_wheel_metadata():
    locked = lock(
        wheels=[
            wheel(requires_dist=["sniffio>=1.2; python_version >= '3.8'"]),
            LockedPythonWheel(
                filename="sniffio-1.3.1-py3-none-any.whl",
                project_name="sniffio",
                version="1.3.1",
                sha256="c" * 64,
                size_bytes=5000,
            ),
        ]
    )

    assert locked.wheels[1].requires_dist == ()
    assert locked.wheels[0].requires_dist == ('sniffio>=1.2; python_version >= "3.8"',)


def test_lock_closes_multiple_levels_of_transitive_requirements():
    locked = lock(
        wheels=[
            wheel(requires_dist=["sniffio>=1"]),
            LockedPythonWheel(
                filename="sniffio-1.3.1-py3-none-any.whl",
                project_name="sniffio",
                version="1.3.1",
                sha256="c" * 64,
                size_bytes=5000,
                requires_dist=["outcome>=1"],
            ),
            LockedPythonWheel(
                filename="outcome-1.3.0-py3-none-any.whl",
                project_name="outcome",
                version="1.3.0",
                sha256="d" * 64,
                size_bytes=6000,
            ),
        ]
    )

    assert {wheel.project_name for wheel in locked.wheels} == {"idna", "sniffio", "outcome"}


@pytest.mark.parametrize(
    ("root_dependencies", "dependency_version", "extra_wheels"),
    [
        (["sniffio>=1"], None, []),
        (["sniffio>=2"], "1.3.1", []),
        ([], None, ["sniffio"]),
    ],
)
def test_lock_rejects_incomplete_or_unreachable_dependency_graph(
    root_dependencies, dependency_version, extra_wheels
):
    wheels = [wheel(requires_dist=root_dependencies)]
    if dependency_version is not None:
        wheels.append(
            LockedPythonWheel(
                filename=f"sniffio-{dependency_version}-py3-none-any.whl",
                project_name="sniffio",
                version=dependency_version,
                sha256="c" * 64,
                size_bytes=5000,
            )
        )
    for name in extra_wheels:
        wheels.append(
            LockedPythonWheel(
                filename=f"{name}-1.0-py3-none-any.whl",
                project_name=name,
                version="1.0",
                sha256="d" * 64,
                size_bytes=5000,
            )
        )

    with pytest.raises(ValidationError):
        lock(wheels=wheels)


def test_lock_evaluates_transitive_markers_for_the_target():
    locked = lock(wheels=[wheel(requires_dist=["sniffio>=1; sys_platform == 'win32'"])])

    assert len(locked.wheels) == 1


def test_lock_bounds_aggregate_requires_dist_entries():
    dependencies = [
        LockedPythonWheel(
            filename=f"dep_{index}-1.0-py3-none-any.whl",
            project_name=f"dep-{index}",
            version="1.0",
            sha256=f"{index + 1:064x}",
            size_bytes=5000,
            requires_dist=["idna>=3"] * 256,
        )
        for index in range(17)
    ]

    with pytest.raises(ValidationError, match="lock graph limit"):
        lock(wheels=[wheel(), *dependencies])


@pytest.mark.parametrize(
    "requires_dist", [["sniffio @ https://example.invalid/a.whl"], ["sniffio[fast]>=1"]]
)
def test_wheel_metadata_rejects_direct_urls_and_unsupported_extras(requires_dist):
    with pytest.raises(ValidationError):
        wheel(requires_dist=requires_dist)


def test_lock_copies_inputs_into_immutable_collections():
    requirements = ["idna>=3"]
    wheels = [wheel()]
    target_value = target()
    locked = lock(requirements=requirements, wheels=wheels)

    requirements.append("other>=1")
    wheels.clear()

    assert len(locked.requirements) == 1
    assert len(locked.wheels) == 1
    assert isinstance(locked.target.environment, Mapping)
    assert isinstance(target_value.model_dump(mode="json")["environment"], dict)
    with pytest.raises(TypeError):
        locked.target.environment["python_version"] = "3.99"

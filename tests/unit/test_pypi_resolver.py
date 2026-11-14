from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from xaa.extensions.procedure_dependencies import PythonDependencyTarget
from xaa.extensions.pypi_resolver import (
    PyPIResolutionError,
    PyPIWheelResolver,
    validate_pypi_broker_url,
)


def _file(
    name: str,
    version: str,
    metadata: bytes,
    *,
    tag: str = "py3-none-any",
    **overrides: object,
) -> dict[str, Any]:
    filename = f"{name.replace('-', '_')}-{version}-{tag}.whl"
    value: dict[str, Any] = {
        "filename": filename,
        "url": f"https://files.pythonhosted.org/packages/test/{filename}",
        "hashes": {"sha256": "a" * 64},
        "size": 1024,
        "core-metadata": {"sha256": hashlib.sha256(metadata).hexdigest()},
        "requires-python": ">=3.8",
    }
    value.update(overrides)
    return value


def _metadata(name: str, version: str, *requires: str) -> bytes:
    body = f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n"
    body += "".join(f"Requires-Dist: {value}\n" for value in requires)
    return (body + "\n").encode()


def _pages(
    projects: Mapping[str, list[tuple[dict[str, Any], bytes]]],
    *,
    fail: str | None = None,
):
    async def serve(request: httpx.Request) -> httpx.Response:
        project = request.url.path.rsplit("/", 2)[-2]
        if request.url.host == "pypi.org":
            if project == fail:
                return httpx.Response(302, headers={"location": "https://evil.example/"})
            rows = projects.get(project, [])
            files = [row[0] for row in rows]
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.pypi.simple.v1+json"},
                json={"name": project, "files": files},
            )
        filename = request.url.path.rsplit("/", 1)[-1].removesuffix(".metadata")
        for rows in projects.values():
            for entry, metadata in rows:
                if entry["filename"] == filename:
                    return httpx.Response(200, content=metadata)
        return httpx.Response(404)

    return httpx.MockTransport(serve)


def _target() -> PythonDependencyTarget:
    return PythonDependencyTarget(
        distribution="Ubuntu-24.04",
        environment={
            "implementation_name": "cpython",
            "implementation_version": "3.12.3",
            "os_name": "posix",
            "platform_machine": "x86_64",
            "platform_python_implementation": "CPython",
            "platform_release": "6.8.0-wsl",
            "platform_system": "Linux",
            "platform_version": "fixture",
            "python_full_version": "3.12.3",
            "python_version": "3.12",
            "sys_platform": "linux",
        },
        compatible_wheel_tags=("py3-none-any",),
    )


def test_network_broker_allows_only_bounded_https_pypi_and_wheel_urls():
    assert validate_pypi_broker_url("https://pypi.org/simple/requests/")[1] == 2 * 1024 * 1024
    assert (
        validate_pypi_broker_url(
            "https://files.pythonhosted.org/packages/fixture/requests-1-py3-none-any.whl.metadata"
        )[1]
        == 1024 * 1024
    )
    assert (
        validate_pypi_broker_url(
            "https://files.pythonhosted.org/packages/fixture/requests-1-py3-none-any.whl"
        )[1]
        == 64 * 1024 * 1024
    )
    for value in (
        "http://pypi.org/simple/requests/",
        "https://evil.example/simple/requests/",
        "https://pypi.org:444/simple/requests/",
        "https://pypi.org:bad/simple/requests/",
        "https://pypi.org/simple/../requests/",
        "https://pypi.org/simple/%2e%2e/requests/",
        "https://pypi.org/simple/requests/?next=https://evil.example/",
        "https://files.pythonhosted.org/packages/fixture/requests.tar.gz",
    ):
        with pytest.raises(PyPIResolutionError):
            validate_pypi_broker_url(value)


@pytest.mark.asyncio
async def test_resolver_selects_transitive_compatible_versions_and_builds_review_lock():
    top_two = _metadata("xaa-top", "2.0", "xaa-leaf<3")
    top_one = _metadata("xaa-top", "1.0")
    leaf_three = _metadata("xaa-leaf", "3.0")
    leaf_two = _metadata("xaa-leaf", "2.0")
    projects = {
        "xaa-top": [
            (_file("xaa-top", "2.0", top_two), top_two),
            (_file("xaa-top", "1.0", top_one), top_one),
        ],
        "xaa-leaf": [
            (_file("xaa-leaf", "3.0", leaf_three), leaf_three),
            (_file("xaa-leaf", "2.0", leaf_two), leaf_two),
        ],
    }
    resolver = PyPIWheelResolver(transport=_pages(projects))

    lock = await resolver.resolve(
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="b" * 64,
        target=_target(),
        requirements=("xaa-top>=1",),
    )

    assert {wheel.project_name: wheel.version for wheel in lock.wheels} == {
        "xaa-leaf": "2.0",
        "xaa-top": "2.0",
    }
    assert next(
        wheel for wheel in lock.wheels if wheel.project_name == "xaa-top"
    ).requires_dist == ("xaa-leaf<3",)
    assert lock.requirements == ("xaa-top>=1",)


@pytest.mark.asyncio
async def test_resolver_backtracks_when_transitive_constraints_conflict():
    top_two = _metadata("xaa-top", "2.0", "xaa-leaf<2")
    top_one = _metadata("xaa-top", "1.0")
    leaf_two = _metadata("xaa-leaf", "2.0")
    projects = {
        "xaa-top": [
            (_file("xaa-top", "2.0", top_two), top_two),
            (_file("xaa-top", "1.0", top_one), top_one),
        ],
        "xaa-leaf": [(_file("xaa-leaf", "2.0", leaf_two), leaf_two)],
    }

    lock = await PyPIWheelResolver(transport=_pages(projects)).resolve(
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="b" * 64,
        target=_target(),
        requirements=("xaa-top>=1", "xaa-leaf>=2"),
    )

    assert {wheel.project_name: wheel.version for wheel in lock.wheels} == {
        "xaa-top": "1.0",
        "xaa-leaf": "2.0",
    }


@pytest.mark.asyncio
async def test_resolver_intersects_multiple_direct_constraints_for_one_project():
    latest = _metadata("xaa-top", "2.0")
    supported = _metadata("xaa-top", "1.0")
    projects = {
        "xaa-top": [
            (_file("xaa-top", "2.0", latest), latest),
            (_file("xaa-top", "1.0", supported), supported),
        ]
    }

    lock = await PyPIWheelResolver(transport=_pages(projects)).resolve(
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="b" * 64,
        target=_target(),
        requirements=("xaa-top>=1", "xaa-top<2", "xaa-top>=1"),
    )

    assert lock.wheels[0].version == "1.0"
    assert lock.requirements == ("xaa-top<2", "xaa-top>=1")


@pytest.mark.asyncio
async def test_resolver_uses_target_marker_environment_for_transitive_requirements():
    top = _metadata("xaa-top", "1.0", 'xaa-win; sys_platform == "win32"')
    projects = {"xaa-top": [(_file("xaa-top", "1.0", top), top)]}

    lock = await PyPIWheelResolver(transport=_pages(projects)).resolve(
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="b" * 64,
        target=_target(),
        requirements=("xaa-top",),
    )

    assert [wheel.project_name for wheel in lock.wheels] == ["xaa-top"]


@pytest.mark.asyncio
async def test_resolver_prefers_target_tag_order_for_equal_versions():
    portable = _metadata("xaa-tagged", "1.0")
    specific = _metadata("xaa-tagged", "1.0")
    tags = ("py312-none-any", "py3-none-any")
    target = _target().model_copy(update={"compatible_wheel_tags": tags})
    projects = {
        "xaa-tagged": [
            (_file("xaa-tagged", "1.0", portable, tag="py3-none-any"), portable),
            (_file("xaa-tagged", "1.0", specific, tag="py312-none-any"), specific),
        ]
    }

    lock = await PyPIWheelResolver(transport=_pages(projects)).resolve(
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="b" * 64,
        target=target,
        requirements=("xaa-tagged",),
    )

    assert lock.wheels[0].filename.endswith("py312-none-any.whl")
    assert lock.target.compatible_wheel_tags == tags
    assert json.loads(lock.canonical_json())["target"]["compatible_wheel_tags"] == sorted(tags)


@pytest.mark.asyncio
async def test_resolver_applies_exact_version_constraints_before_candidate_bound():
    releases: list[tuple[dict[str, Any], bytes]] = []
    for major in range(25, 0, -1):
        version = f"{major}.0"
        metadata = _metadata("xaa-pinned", version)
        releases.append((_file("xaa-pinned", version, metadata), metadata))
    projects = {"xaa-pinned": releases}

    lock = await PyPIWheelResolver(transport=_pages(projects)).resolve(
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="b" * 64,
        target=_target(),
        requirements=("xaa-pinned==1.0",),
    )

    assert lock.wheels[0].version == "1.0"


@pytest.mark.asyncio
async def test_resolver_rejects_metadata_hash_mismatch_and_unsafe_project_redirect():
    actual = _metadata("xaa-top", "1.0")
    entry = _file("xaa-top", "1.0", actual)
    entry["core-metadata"] = {"sha256": "c" * 64}
    projects = {"xaa-top": [(entry, actual)]}
    resolver = PyPIWheelResolver(transport=_pages(projects))

    with pytest.raises(PyPIResolutionError, match="No compatible wheel set"):
        await resolver.resolve(
            skill_id="12345678-1234-4234-8234-123456789abc",
            skill_version=1,
            source_sha256="b" * 64,
            target=_target(),
            requirements=("xaa-top",),
        )

    with pytest.raises(PyPIResolutionError, match="metadata request was refused"):
        await PyPIWheelResolver(transport=_pages({}, fail="xaa-top")).resolve(
            skill_id="12345678-1234-4234-8234-123456789abc",
            skill_version=1,
            source_sha256="b" * 64,
            target=_target(),
            requirements=("xaa-top",),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("requirement", ["xaa-top @ https://example.com/top.whl", "xaa-top[extra]"])
async def test_resolver_rejects_direct_urls_and_extras(requirement: str):
    resolver = PyPIWheelResolver(transport=_pages({}))
    with pytest.raises(PyPIResolutionError, match="Direct URLs and package extras"):
        await resolver.resolve(
            skill_id="12345678-1234-4234-8234-123456789abc",
            skill_version=1,
            source_sha256="b" * 64,
            target=_target(),
            requirements=(requirement,),
        )


@pytest.mark.asyncio
async def test_resolver_rejects_unsatisfiable_requirements_without_partial_lock():
    one = _metadata("xaa-top", "1.0")
    projects = {"xaa-top": [(_file("xaa-top", "1.0", one), one)]}
    resolver = PyPIWheelResolver(transport=_pages(projects))

    with pytest.raises(PyPIResolutionError, match="No compatible wheel set"):
        await resolver.resolve(
            skill_id="12345678-1234-4234-8234-123456789abc",
            skill_version=1,
            source_sha256="b" * 64,
            target=_target(),
            requirements=("xaa-top>=2",),
        )

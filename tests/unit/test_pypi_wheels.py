import hashlib
import io
import json
import zipfile

import httpx
import pytest
from xaa.extensions.procedure_dependencies import (
    LockedPythonWheel,
    ProcedurePythonDependencyLock,
    PythonDependencyTarget,
)
from xaa.extensions.pypi_wheels import PyPIAcquisitionError, PyPIWheelAcquirer


def wheel_bytes(*, package: str = "idna", version: str = "3.10") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        prefix = f"{package}-{version}.dist-info"
        archive.writestr(
            f"{prefix}/METADATA",
            f"Metadata-Version: 2.3\nName: {package}\nVersion: {version}\n\n",
        )
        archive.writestr(
            f"{prefix}/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
        )
        archive.writestr(f"{package}/__init__.py", "")
    return output.getvalue()


def make_lock(content: bytes | None = None) -> ProcedurePythonDependencyLock:
    artifact = wheel_bytes() if content is None else content
    record = LockedPythonWheel(
        filename="idna-3.10-py3-none-any.whl",
        project_name="idna",
        version="3.10",
        sha256=hashlib.sha256(artifact).hexdigest(),
        size_bytes=len(artifact),
    )
    environment = {
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
    }
    return ProcedurePythonDependencyLock(
        schema_version=1,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="a" * 64,
        target=PythonDependencyTarget(
            distribution="Ubuntu-24.04",
            environment=environment,
            compatible_wheel_tags=("py3-none-any",),
        ),
        requirements=("idna>=3,<4",),
        wheels=(record,),
    )


def pypi_page(lock: ProcedurePythonDependencyLock, **overrides: object) -> bytes:
    wheel = lock.wheels[0]
    entry: dict[str, object] = {
        "filename": wheel.filename,
        "url": (
            "https://files.pythonhosted.org/packages/fixture/"
            f"{wheel.filename}#sha256={wheel.sha256}"
        ),
        "hashes": {"sha256": wheel.sha256},
        "size": wheel.size_bytes,
    }
    entry.update(overrides)
    return json.dumps({"name": "idna", "files": [entry]}).encode()


@pytest.mark.asyncio
async def test_acquirer_fetches_only_exact_pypi_files_and_verifies_wheel_bytes():
    content = wheel_bytes()
    lock = make_lock(content)
    seen: list[tuple[str, str]] = []

    async def serve(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path))
        if request.url.host == "pypi.org":
            assert request.headers["accept"] == "application/vnd.pypi.simple.v1+json"
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.pypi.simple.v1+json"},
                content=pypi_page(lock, **{"requires-python": ">=3.10"}),
            )
        assert request.url.host == "files.pythonhosted.org"
        return httpx.Response(200, headers={"content-length": str(len(content))}, content=content)

    artifacts = await PyPIWheelAcquirer(transport=httpx.MockTransport(serve)).acquire(lock)

    assert artifacts == {lock.wheels[0].filename: content}
    assert [host for host, _ in seen] == ["pypi.org", "files.pythonhosted.org"]


@pytest.mark.asyncio
async def test_acquirer_streams_verified_wheel_to_disk(tmp_path):
    content = wheel_bytes()
    lock = make_lock(content)

    async def serve(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pypi.org":
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.pypi.simple.v1+json"},
                content=pypi_page(lock),
            )
        return httpx.Response(200, headers={"content-length": str(len(content))}, content=content)

    destination = tmp_path / "wheel-stage"
    paths = await PyPIWheelAcquirer(transport=httpx.MockTransport(serve)).acquire_to_directory(
        lock, destination
    )

    assert paths == {lock.wheels[0].filename: destination / lock.wheels[0].filename}
    assert paths[lock.wheels[0].filename].read_bytes() == content


@pytest.mark.asyncio
async def test_acquirer_streaming_hash_failure_removes_partial_wheel(tmp_path):
    content = wheel_bytes()
    lock = make_lock(content)
    wrong = content[:-1] + bytes([content[-1] ^ 1])

    async def serve(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pypi.org":
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.pypi.simple.v1+json"},
                content=pypi_page(lock),
            )
        return httpx.Response(200, content=wrong)

    destination = tmp_path / "wheel-stage"
    with pytest.raises(PyPIAcquisitionError, match="SHA-256"):
        await PyPIWheelAcquirer(transport=httpx.MockTransport(serve)).acquire_to_directory(
            lock, destination
        )
    assert list(destination.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hashes": {"sha256": "f" * 64}}, "hash does not match"),
        ({"size": 1}, "size does not match"),
        ({"requires-python": ">=3.13"}, "does not support"),
        ({"yanked": "withdrawn"}, "yanked"),
        ({"url": "https://evil.example/idna-3.10-py3-none-any.whl"}, "allowed file host"),
        (
            {"url": "https://files.pythonhosted.org/idna-3.10-py3-none-any.whl?token=x"},
            "allowed file host",
        ),
    ],
)
async def test_acquirer_rejects_untrusted_index_file_records(
    overrides: dict[str, object], message: str
):
    lock = make_lock()

    async def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/vnd.pypi.simple.v1+json"},
            content=pypi_page(lock, **overrides),
        )

    with pytest.raises(PyPIAcquisitionError, match=message):
        await PyPIWheelAcquirer(transport=httpx.MockTransport(serve)).acquire(lock)


@pytest.mark.asyncio
async def test_acquirer_refuses_redirects_and_oversized_project_pages():
    lock = make_lock()

    async def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://files.pythonhosted.org/"})

    with pytest.raises(PyPIAcquisitionError, match="metadata request was refused"):
        await PyPIWheelAcquirer(transport=httpx.MockTransport(redirect)).acquire(lock)

    async def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/vnd.pypi.simple.v1+json"},
            content=b" " * (2 * 1024 * 1024 + 1),
        )

    with pytest.raises(PyPIAcquisitionError, match="exceeded its declared size limit"):
        await PyPIWheelAcquirer(transport=httpx.MockTransport(oversized)).acquire(lock)


@pytest.mark.asyncio
async def test_acquirer_requires_the_pep_691_json_media_type():
    lock = make_lock()

    async def wrong_media_type(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"{}")

    with pytest.raises(PyPIAcquisitionError, match="metadata request was refused"):
        await PyPIWheelAcquirer(transport=httpx.MockTransport(wrong_media_type)).acquire(lock)


@pytest.mark.asyncio
async def test_acquirer_fails_if_downloaded_bytes_do_not_match_pypi_digest():
    expected = wheel_bytes()
    wrong_bytes = bytearray(expected)
    wrong_bytes[-1] ^= 1
    wrong = bytes(wrong_bytes)
    lock = make_lock(expected)

    async def serve(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pypi.org":
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.pypi.simple.v1+json"},
                content=pypi_page(lock),
            )
        return httpx.Response(200, content=wrong)

    with pytest.raises(PyPIAcquisitionError, match="SHA-256 does not match"):
        await PyPIWheelAcquirer(transport=httpx.MockTransport(serve)).acquire(lock)

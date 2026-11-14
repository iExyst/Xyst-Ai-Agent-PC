import hashlib
import io
import zipfile

import pytest
from xaa.extensions.procedure_dependencies import (
    LockedPythonWheel,
    ProcedurePythonDependencyLock,
    PythonDependencyTarget,
)
from xaa.extensions.wheelhouse_storage import ProcedureWheelhouseStore, WheelhouseStorageError


def wheel_bytes() -> bytes:
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


def lock_for(content: bytes) -> ProcedurePythonDependencyLock:
    return ProcedurePythonDependencyLock(
        schema_version=1,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="a" * 64,
        target=PythonDependencyTarget(
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
        ),
        requirements=("idna>=3,<4",),
        wheels=(
            LockedPythonWheel(
                filename="idna-3.10-py3-none-any.whl",
                project_name="idna",
                version="3.10",
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            ),
        ),
    )


def test_wheelhouse_store_is_atomic_idempotent_and_lock_bound(tmp_path):
    content = wheel_bytes()
    lock = lock_for(content)
    store = ProcedureWheelhouseStore(tmp_path / "wheelhouses")
    assert not store.present(lock)

    saved = store.store(lock, {lock.wheels[0].filename: content})

    assert saved == {lock.wheels[0].filename: content}
    assert store.present(lock)
    assert store.read(lock) == saved
    assert store.store(lock, saved) == saved
    assert {
        item.name for item in (tmp_path / "wheelhouses" / lock.environment_digest()).iterdir()
    } == {
        lock.wheels[0].filename,
        "manifest.json",
    }


def test_wheelhouse_store_streams_verified_paths_atomically(tmp_path):
    content = wheel_bytes()
    lock = lock_for(content)
    source = tmp_path / "staging" / lock.wheels[0].filename
    source.parent.mkdir()
    source.write_bytes(content)
    store = ProcedureWheelhouseStore(tmp_path / "wheelhouses")

    store.store_files(lock, {source.name: source})

    assert store.present(lock)
    assert store.read(lock) == {source.name: content}
    store.store_files(lock, {source.name: source})
    source.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    with pytest.raises(WheelhouseStorageError, match="do not match"):
        store.store_files(lock, {source.name: source})


def test_wheelhouse_store_rechecks_artifact_and_manifest_bytes(tmp_path):
    content = wheel_bytes()
    lock = lock_for(content)
    store = ProcedureWheelhouseStore(tmp_path / "wheelhouses")
    directory = store.root / lock.environment_digest()
    store.store(lock, {lock.wheels[0].filename: content})

    wheel_path = directory / lock.wheels[0].filename
    wheel_path.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    with pytest.raises(WheelhouseStorageError, match="failed integrity verification"):
        store.read(lock)


def test_wheelhouse_store_rejects_extra_or_incomplete_artifacts(tmp_path):
    content = wheel_bytes()
    lock = lock_for(content)
    store = ProcedureWheelhouseStore(tmp_path / "wheelhouses")

    with pytest.raises(WheelhouseStorageError, match="do not match"):
        store.store(lock, {"other.whl": content})

    store.store(lock, {lock.wheels[0].filename: content})
    (store.root / lock.environment_digest() / "extra.txt").write_text("unexpected")
    assert not store.present(lock)
    with pytest.raises(WheelhouseStorageError, match="unexpected files"):
        store.read(lock)

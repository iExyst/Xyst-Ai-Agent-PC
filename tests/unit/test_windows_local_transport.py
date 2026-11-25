from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from xaa.runtime import mcp_appcontainer
from xaa.runtime import windows_local_transport as transport


class _ScandirResult:
    def __init__(self, entries: list[Any]) -> None:
        self.entries = entries

    def __enter__(self) -> Any:
        return iter(self.entries)

    def __exit__(self, *_: object) -> None:
        return None


class _DisappearingEntry:
    def __init__(self, path: Path, *, directory: bool = False) -> None:
        self.path = str(path)
        self.directory = directory

    def stat(self, *, follow_symlinks: bool) -> Any:
        assert follow_symlinks is False
        if self.directory:
            return SimpleNamespace(st_file_attributes=0, st_size=0)
        raise FileNotFoundError(self.path)

    def is_dir(self, *, follow_symlinks: bool) -> bool:
        assert follow_symlinks is False
        return self.directory


def test_stable_code_fingerprint_ignores_nested_runtime_specialization() -> None:
    def outer():
        def inner(value: int) -> int:
            return value + 1

        return inner

    code = outer.__code__
    before = hashlib.sha256(transport._stable_marshaled_code(code)).hexdigest()  # pyright: ignore[reportPrivateUsage]
    inner = outer()
    for value in range(1000):
        inner(value)
    after = hashlib.sha256(transport._stable_marshaled_code(code)).hexdigest()  # pyright: ignore[reportPrivateUsage]

    assert after == before


def test_appcontainer_stable_fingerprint_ignores_nested_runtime_specialization() -> None:
    def outer():
        def inner(value: int) -> int:
            return value + 1

        return inner

    code = outer.__code__
    before = hashlib.sha256(
        mcp_appcontainer._stable_marshaled_code(code)  # pyright: ignore[reportPrivateUsage]
    ).hexdigest()
    inner = outer()
    for value in range(1000):
        inner(value)
    after = hashlib.sha256(
        mcp_appcontainer._stable_marshaled_code(code)  # pyright: ignore[reportPrivateUsage]
    ).hexdigest()

    assert after == before


def test_frozen_appcontainer_fingerprint_uses_stable_code_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_appcontainer.sys, "frozen", True, raising=False)
    code_objects = mcp_appcontainer._appcontainer_shim_code_objects()  # pyright: ignore[reportPrivateUsage]
    expected = hashlib.sha256(
        b"".join(
            mcp_appcontainer._stable_marshaled_code(code)  # pyright: ignore[reportPrivateUsage]
            for code in code_objects
        )
    ).hexdigest()

    assert mcp_appcontainer.appcontainer_shim_fingerprint() == expected


def test_frozen_engine_children_get_independent_pyinstaller_runtime_state(tmp_path: Path) -> None:
    shim_environment = {"PATH": "runtime"}
    command_environment = {"PATH": "runtime"}
    engine = tmp_path / "xaa-engine.exe"

    transport._configure_frozen_runtime_environments(  # pyright: ignore[reportPrivateUsage]
        shim_environment,
        command_environment,
        engine,
        engine_executable=engine,
        frozen=True,
    )

    assert shim_environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert command_environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_external_command_does_not_receive_pyinstaller_runtime_state(tmp_path: Path) -> None:
    shim_environment = {"PATH": "runtime"}
    command_environment = {"PATH": "runtime"}

    transport._configure_frozen_runtime_environments(  # pyright: ignore[reportPrivateUsage]
        shim_environment,
        command_environment,
        tmp_path / "findstr.exe",
        engine_executable=tmp_path / "xaa-engine.exe",
        frozen=True,
    )

    assert shim_environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in command_environment


@pytest.mark.parametrize("disappears_during", ["stat", "scandir"])
def test_private_storage_scan_tolerates_transient_frozen_runtime_cleanup(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disappears_during: str,
) -> None:
    nested = workspace_tmp_path / "greenlet-3.5.6.dist-info"
    original_scandir = transport.os.scandir

    def scan(path: str | Path) -> Any:
        if Path(path) == workspace_tmp_path:
            entry = _DisappearingEntry(nested, directory=disappears_during == "scandir")
            return _ScandirResult([entry])
        if Path(path) == nested:
            raise FileNotFoundError(nested)
        return original_scandir(path)

    monkeypatch.setattr(transport.os, "scandir", scan)

    assert transport._private_workspace_size(workspace_tmp_path) == 0  # pyright: ignore[reportPrivateUsage]


def test_private_runtime_scan_retries_transient_windows_access_denial(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_scandir = transport.os.scandir
    attempts = 0

    def scan(path: str | Path) -> Any:
        nonlocal attempts
        if Path(path) == workspace_tmp_path and attempts == 0:
            attempts += 1
            error = PermissionError("sharing access race")
            error.winerror = 5  # type: ignore[attr-defined]
            raise error
        return original_scandir(path)

    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setattr(transport.os, "scandir", scan)
    monkeypatch.setattr(transport.time, "sleep", lambda _delay: None)

    assert transport._private_runtime_size(workspace_tmp_path, max_bytes=1024) == 0  # pyright: ignore[reportPrivateUsage]
    assert attempts == 1


def test_private_runtime_scan_fails_closed_after_persistent_access_denial(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def scan(_path: str | Path) -> Any:
        nonlocal attempts
        attempts += 1
        error = PermissionError("persistent access denial")
        error.winerror = 5  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setattr(transport.os, "scandir", scan)
    monkeypatch.setattr(transport.time, "sleep", lambda _delay: None)

    with pytest.raises(PermissionError, match="persistent access denial"):
        transport._private_runtime_size(workspace_tmp_path, max_bytes=1024)  # pyright: ignore[reportPrivateUsage]
    assert attempts == 5

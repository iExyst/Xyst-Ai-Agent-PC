from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
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

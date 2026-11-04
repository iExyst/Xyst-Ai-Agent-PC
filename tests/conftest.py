from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def workspace_tmp_path() -> Iterator[Path]:
    root = Path(__file__).resolve().parents[1] / ".test-tmp"
    directory = root / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    try:
        yield directory
    finally:
        if directory.parent == root and directory.is_dir():

            def remove_readonly(function, path, error):
                if not isinstance(error, PermissionError):
                    raise error
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                function(path)

            shutil.rmtree(directory, onexc=remove_readonly)

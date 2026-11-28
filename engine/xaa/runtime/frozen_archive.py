"""Inspect generated PyInstaller archives before shipping Windows builds."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from xaa.runtime.execution_limits import MAX_SHIM_RUNTIME_BYTES


def verify_frozen_runtime_size(executable: Path) -> int:
    """Fail packaging when the extracted one-file runtime exceeds AppContainer's shim cap."""
    readers = importlib.import_module("PyInstaller.archive.readers")
    archive: Any = readers.CArchiveReader(str(executable))
    expanded_bytes = 0
    for name in archive.toc:
        member: bytes = archive.extract(name)
        expanded_bytes += len(member)
        if expanded_bytes > MAX_SHIM_RUNTIME_BYTES:
            maximum_mib = MAX_SHIM_RUNTIME_BYTES // (1024 * 1024)
            expanded_mib = expanded_bytes / (1024 * 1024)
            raise RuntimeError(
                f"Frozen engine expands beyond the {maximum_mib} MiB Windows AppContainer "
                f"runtime limit ({expanded_mib:.2f} MiB)."
            )
    return expanded_bytes

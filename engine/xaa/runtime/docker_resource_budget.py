"""Cross-process admission for containers created by X.A.A.'s local Docker backend."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO


def docker_budget_lock_path() -> Path:
    """Use one per-user lock location shared by all X.A.A. data profiles."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "XAA" / "docker-resource-budget.lock"
    return Path.home() / ".xaa" / "docker-resource-budget.lock"


def _try_lock(path: Path) -> BinaryIO | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                stream.close()
                return None
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                stream.close()
                return None
        return stream
    except BaseException:
        if not stream.closed:
            stream.close()
        raise


def _unlock(stream: BinaryIO) -> None:
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


@asynccontextmanager
async def docker_container_creation_lock(*, deadline: float) -> AsyncGenerator[None]:
    """Serialize local container inventory and create across engine processes."""
    path = docker_budget_lock_path()
    stream: BinaryIO | None = None
    try:
        while time.monotonic() < deadline:
            stream = _try_lock(path)
            if stream is not None:
                break
            await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
        if stream is None:
            raise TimeoutError("Timed out waiting for local Docker resource admission.")
        yield
    finally:
        if stream is not None:
            _unlock(stream)

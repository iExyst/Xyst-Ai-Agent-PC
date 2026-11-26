"""Per-user process-wide admission for X.A.A. local inference clients."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO


def inference_process_lock_path() -> Path:
    """Return one lock location shared by X.A.A. processes for this OS user."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data and Path(local_app_data).is_absolute():
        return Path(local_app_data) / "XAA" / "lm-studio-inference.lock"
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home and Path(state_home).is_absolute():
        return Path(state_home) / "xaa" / "lm-studio-inference.lock"
    return Path.home() / ".local" / "state" / "xaa" / "lm-studio-inference.lock"


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


def release_inference_process_lock(stream: BinaryIO) -> None:
    """Release a lock returned by :func:`acquire_inference_process_lock`."""
    _unlock(stream)


def other_process_holds_inference_lock(path: Path | None, *, locally_active: bool) -> bool | None:
    """Return whether another process owns the slot, or ``None`` if unavailable."""
    if locally_active:
        return False
    if path is None:
        return None
    try:
        stream = _try_lock(path)
    except OSError:
        return None
    if stream is None:
        return True
    _unlock(stream)
    return False


async def acquire_inference_process_lock(path: Path, *, deadline: float) -> BinaryIO:
    """Acquire a nonblocking OS file lock, waiting cooperatively until deadline."""
    while time.monotonic() < deadline:
        stream = _try_lock(path)
        if stream is not None:
            return stream
        await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
    raise TimeoutError("Timed out waiting for another X.A.A. inference process.")


@asynccontextmanager
async def inference_process_lock(path: Path, *, deadline: float) -> AsyncGenerator[None]:
    """Hold a nonblocking OS file lock until the context exits."""
    stream = await acquire_inference_process_lock(path, deadline=deadline)
    try:
        yield
    finally:
        _unlock(stream)
